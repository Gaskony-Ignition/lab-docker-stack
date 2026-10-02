#!/usr/bin/env bash
#
# Which Ignition build the four gateways run, and how to change it.
#
#   scripts/ign-version.sh                 report: declared, overridden, RUNNING
#   scripts/ign-version.sh 8.3.10          switch all four gateways
#   scripts/ign-version.sh default         drop the override, back to the repo's
#   make ignition-version [SET=8.3.10]     the same thing
#
# WHY THIS IS ONE KNOB FOR FOUR GATEWAYS AND NOT FOUR KNOBS. A redundant pair on
# two versions does not sync -- the backup already reports Incompatible and
# restarts itself trying -- and EAM's Send Project to an edge on another minor
# is a gamble nobody should take mid-demonstration. There is no case for the
# hub and an edge disagreeing, so the tool cannot express one, and `make
# validate` fails a set that does.
#
# WHAT IT WRITES. The compose files carry ${IGNITION_VERSION:-<v>}: the DEFAULT
# is the repo's declared version, committed, and what validate and
# watch-upstream.sh read. This writes the per-machine OVERRIDE into ONE
# gitignored file, .wd-local/ignition-version, which stack.sh exports for every
# compose call. So trying a build here does not commit the repo to it; bumping
# the compose default is the separate, deliberate act of adopting it.
#
# ONE FILE RATHER THAN FOUR .env ENTRIES, for two reasons found by building the
# console picker on top of this. wd-control mounts the repo READ-ONLY and holds
# the Docker socket, so the demo console could not write a stack's .env without
# opening the whole repo to it -- and .wd-local is the one narrow rw mount that
# avoids that. Second: four files that must agree can disagree. One cannot, so
# the mismatch state is impossible for an override by construction.
#
# ==========================================================================
# THE DATA VOLUME IS A ONE-WAY DOOR, AND THAT IS THE WHOLE REASON FOR THE
# GUARDS BELOW.
#
# A gateway upgrades its internal configuration store the first time a newer
# build starts against a volume, and the older build cannot then read it. The
# volume holds everything this repo deliberately does not own -- commissioning,
# Gateway Network pairing, EAM registration, the database connection, MQTT
# config, the redundancy pair, the security zone. So going *back* is not a
# version change, it is a rebuild.
#
# The way that hurts is specific: nothing complains at switch time. It
# complains when a gateway will not start, which is ten minutes before you
# show somebody something. So a downgrade is REFUSED here, at the moment it is
# cheap to reconsider, rather than discovered later.
# ==========================================================================
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The gateways, from the manifests. Deriving it means a fifth gateway is
# picked up by adding a folder, like everything else in this repo.
GWS="$(gateways)"

MIN_SERIES="8.3"
SEEN="$REPO_ROOT/.ignition-version-seen"

series_of() { printf '%s' "$(echo "$1" | cut -d. -f1-2)"; }

# True when series A is older than series B. Numeric per component, so a
# future calendar series (2027.1) correctly reads as NEWER than 8.3 rather
# than older -- 2027 > 8. Same comparison validate.py and watch-upstream.sh
# make; all three have to agree or the floor means different things.
series_lt() {
  local am="${1%%.*}" an="${1##*.}" bm="${2%%.*}" bn="${2##*.}"
  [ "$am" -lt "$bm" ] && return 0
  [ "$am" -eq "$bm" ] && [ "$an" -lt "$bn" ] && return 0
  return 1
}

# --- what each gateway is set to ---------------------------------------------

declared_for() {  # the compose DEFAULT -- the repo's declared version
  sed -n 's|.*image: *inductiveautomation/ignition:\${IGNITION_VERSION:-\([0-9.]*\)}.*|\1|p' \
    "$STACKS_DIR/$1/compose.yaml" 2>/dev/null | head -1
}

# ONE file for all four gateways, not one .env each -- see lib.sh. Four files
# that must agree can disagree; one cannot, so the "gateways disagree" state is
# impossible for an override by construction. (It is still possible for the
# committed compose DEFAULTS, which is what validate checks.)
override_for() {  # the per-machine override, or empty. Absent is the norm.
  ign_version_override
}

effective_for() {
  local o; o="$(override_for "$1")"
  if [ -n "$o" ]; then printf '%s' "$o"; else declared_for "$1"; fi
}

# What the container was CREATED from -- the honest answer about the volume,
# and it needs no HTTP, no credential and no running gateway: a stopped
# container still answers. `make down` removes containers, though, which is
# the normal resting state here, so a miss is expected rather than an error.
running_for() {
  docker inspect -f '{{.Config.Image}}' "$1" 2>/dev/null \
    | sed 's|.*ignition:||' | tr -d '\n' || true
}

# A machine that has been `down` since before you asked has no container to
# inspect, and the volume still remembers. So every observation is recorded,
# and the record is what the downgrade guard falls back on.
#
# Deliberately NOT written into the gateway's own data volume. That would
# survive a re-clone and could not desync -- but writing into data/ is how
# this repo has broken a gateway before (a root-owned modules.json made every
# later module install fail silently), and a dotfile in a directory the
# gateway scans is not worth that risk for a convenience.
remember() {  # remember <stack> <version>
  [ -n "$2" ] || return 0
  local tmp="$SEEN.tmp"
  { grep -v "^$1 " "$SEEN" 2>/dev/null || true; printf '%s %s\n' "$1" "$2"; } \
    | sort > "$tmp"
  mv "$tmp" "$SEEN"
}

seen_for() {
  [ -f "$SEEN" ] || return 0
  sed -n "s/^$1 //p" "$SEEN" | tail -1
}

# The newest version any of these volumes is known to have run. That is what a
# downgrade has to clear, not the version the repo happens to declare.
highest_seen() {
  local s v hi=""
  for s in $GWS; do
    v="$(running_for "$s")"
    [ -n "$v" ] && remember "$s" "$v"
    [ -z "$v" ] && v="$(seen_for "$s")"
    [ -z "$v" ] && continue
    if [ -z "$hi" ] || version_gt "$v" "$hi"; then hi="$v"; fi
  done
  printf '%s' "$hi"
}

# Full-version compare, component by component, numeric. `sort -V` is GNU-only
# and macOS ships BSD sort -- the same portability trap that `find -printf` and
# `mapfile` already cost this repo.
version_gt() {  # version_gt A B -- true when A is newer than B
  local a b i n
  IFS=. read -r -a a <<< "$1"
  IFS=. read -r -a b <<< "$2"
  n=${#a[@]}; [ ${#b[@]} -gt $n ] && n=${#b[@]}
  for ((i=0; i<n; i++)); do
    local x="${a[i]:-0}" y="${b[i]:-0}"
    case "$x$y" in *[!0-9]*) return 1 ;; esac
    [ "$x" -gt "$y" ] && return 0
    [ "$x" -lt "$y" ] && return 1
  done
  return 1
}

manifest_series() {
  sed -n 's/.*PINNED TO THE IGNITION VERSION[^(]*(\([0-9.]*\)).*/\1/p' \
    "$REPO_ROOT/scripts/modules.manifest" 2>/dev/null | head -1 | cut -d. -f1-2
}

# --- report ------------------------------------------------------------------

report() {
  local s decl over eff run mismatch=0 first=""
  printf '%-18s %-10s %-10s %-10s %s\n' GATEWAY DECLARED OVERRIDE EFFECTIVE RUNNING
  printf '%-18s %-10s %-10s %-10s %s\n' ------- -------- -------- --------- -------
  for s in $GWS; do
    decl="$(declared_for "$s")"; over="$(override_for "$s")"
    eff="$(effective_for "$s")"; run="$(running_for "$s")"
    [ -n "$run" ] && remember "$s" "$run"
    [ -z "$run" ] && run="$(seen_for "$s")" && [ -n "$run" ] && run="$run (last seen)"
    [ -z "$first" ] && first="$eff"
    [ "$eff" != "$first" ] && mismatch=1
    printf '%-18s %-10s %-10s %-10s %s\n' \
      "$s" "${decl:--}" "${over:--}" "${eff:--}" "${run:--}"
  done
  echo
  if [ "$mismatch" -eq 1 ]; then
    warn "the gateways do not agree. A redundant pair on two versions does not
       sync and an EAM push to a mismatched edge is a gamble:
           make ignition-version SET=<version>"
  fi

  local ms; ms="$(manifest_series)"
  dim "  modules are pinned to Ignition $ms -- a patch inside $ms needs no module work,"
  dim "  a change of series needs a matching module set first"
  # An override is invisible to CI and to every other machine, and the way that
  # bites is somebody reporting a bug against "the repo's version" while
  # standing at a gateway running something else.
  local anyover=0
  for s in $GWS; do [ -n "$(override_for "$s")" ] && anyover=1; done
  if [ "$anyover" -eq 1 ]; then
    dim "  an OVERRIDE is local to this machine -- the repo still declares"
    dim "  $(declared_for "$(echo "$GWS" | head -1)"). To adopt it everywhere, change the compose defaults."
  fi
}

# --- switching ---------------------------------------------------------------

set_version() {
  local want="$1" s

  if [ "$want" = default ] || [ "$want" = repo ]; then
    rm -f "$IGN_VERSION_FILE"
    for s in $GWS; do
      local f="$STACKS_DIR/$s/.env"
      if [ -f "$f" ]; then
        grep -v '^IGNITION_VERSION=' "$f" > "$f.tmp" || true
        mv "$f.tmp" "$f"
      fi
    done
    ok "override cleared -- back to the repo's declared $(declared_for "$(echo "$GWS" | head -1)")"
    say "the containers still run what they were created from:"
    dim "    ./wd down && ./wd up"
    return 0
  fi

  case "$want" in
    [0-9]*.[0-9]*) ;;
    *) die "'$want' is not an Ignition version (expected e.g. 8.3.10)" ;;
  esac

  local series; series="$(series_of "$want")"

  # 1. THE FLOOR. 8.1 is a different Perspective, a different resource format
  #    and a different module ABI; nothing here is verified below 8.3.
  if series_lt "$series" "$MIN_SERIES"; then
    die "$want is Ignition $series, and this repo is $MIN_SERIES+ only.
     Nothing here is verified below that -- a different Perspective, a
     different resource format and a different module ABI."
  fi

  # 2. MODULES ARE PER-MINOR, and a mismatch is SILENT: the gateway logs it and
  #    carries on, so the Architecture tab and Site 2's Sparkplug road simply
  #    are not there. A patch inside the pinned series needs nothing.
  local ms; ms="$(manifest_series)"
  if [ -n "$ms" ] && [ "$series" != "$ms" ]; then
    die "the modules here are built for Ignition $ms, and you are asking for
     $series. A module built for another minor does NOT fail loudly -- the
     gateway logs a mismatch and carries on without it, so the Architecture
     tab and Site 2's Sparkplug road would quietly not be there.

     Take a matching module set first (make upgrade-modules), update
     scripts/modules.manifest in the same commit, then set the version."
  fi

  # 3. DOES THE TAG EXIST, FOR THIS MACHINE'S ARCHITECTURE? Asking Docker Hub
  #    costs a second and saves a failed `up` that reads like a broken stack.
  #    The arch half is not theoretical: an image that exists only for amd64
  #    pulls happily on arm64 and then will not run.
  #    NEVER a backtick inside a double-quoted message. One in the "could not
  #    reach Docker Hub" text below read as command substitution and actually
  #    RAN `./wd up` -- the warning string started the stack. Single quotes or
  #    nothing.
  local arch; arch="$(docker version --format '{{.Server.Arch}}' 2>/dev/null || echo '')"
  local mf=""
  if mf="$(docker manifest inspect "inductiveautomation/ignition:$want" 2>/dev/null)"; then
    # `grep -c`, not `grep -q`: an early-exiting grep SIGPIPEs its upstream and
    # the pipeline returns 141, so a real match can read as no match.
    if [ -n "$arch" ] \
       && [ "$(printf '%s' "$mf" | grep -c "\"architecture\": \"$arch\"" || true)" -eq 0 ]; then
      die "inductiveautomation/ignition:$want exists, but not for $arch.
     It would pull and then fail to run."
    fi
    ok "inductiveautomation/ignition:$want exists${arch:+ for $arch}"
  else
    # PROVE THE INSTRUMENT WITH A CONTROL. The lookup failing means one of two
    # very different things -- no such tag, or no route to Docker Hub -- and
    # they need opposite answers. So ask the same question about a tag we know
    # exists: if THAT works, the registry is reachable and the tag is simply
    # wrong, which is worth refusing rather than discovering on the pull.
    local known; known="$(declared_for "$(printf '%s' "$GWS" | head -1)")"
    if [ -n "$known" ] && [ "$known" != "$want" ] \
       && docker manifest inspect "inductiveautomation/ignition:$known" >/dev/null 2>&1; then
      die "there is no inductiveautomation/ignition:$want on Docker Hub.
     (Checked against $known, which resolved -- so this is the tag, not the
     network.) Versions are listed at hub.docker.com/r/inductiveautomation/ignition/tags"
    fi
    warn "could not reach Docker Hub to confirm the tag exists -- carrying on.
       If it is wrong, the next 'up' fails on the pull rather than silently."
  fi

  # 4. THE ONE-WAY DOOR. Checked against the newest build these volumes are
  #    KNOWN to have run, not against what the repo declares -- they are
  #    different numbers the moment anyone has experimented.
  local hi; hi="$(highest_seen)"
  if [ -n "$hi" ] && version_gt "$hi" "$want"; then
    die "these volumes have already run Ignition $hi, and $want is older.

     A gateway upgrades its configuration store on first start and the older
     build cannot read it back -- so this is not a version change, it is a
     rebuild. Everything the repo does not own lives in those volumes:
     commissioning, Gateway Network pairing, EAM, the database connection,
     MQTT config, the redundancy pair, the security zone.

     If you mean it:
         ./wd gwbk                     take a backup while it still starts
         ./wd -- scripts/ign-version.sh $want --force
         ./wd destroy-all && ./wd bootstrap      (15-20 min, a new CA)"
  fi

  # 5. Write it -- once, for all four.
  wd_local_dir >/dev/null
  printf '%s\n' "$want" > "$IGN_VERSION_FILE"
  for s in $GWS; do
    # Clear any leftover from the earlier per-stack arrangement, so a stale
    # line in a .env cannot outlive it and mean something different from what
    # the report shows.
    local f="$STACKS_DIR/$s/.env"
    if [ -f "$f" ] && [ -n "$(grep -c '^IGNITION_VERSION=' "$f" || true)" ]; then
      grep -v '^IGNITION_VERSION=' "$f" > "$f.tmp" || true
      mv "$f.tmp" "$f"
    fi
    # DELIBERATELY NOT `remember` HERE. The record answers "what have these
    # volumes actually run", and setting a version is a statement of intent --
    # nothing has touched a volume until a container starts on it. Recording
    # the intent poisoned the downgrade guard the first time this was run:
    # asking for a version that turned out not to exist left the record
    # claiming the volumes had run it, and every real version was then refused
    # as a downgrade. Only running_for() writes the record.
  done
  ok "all $(printf '%s' "$GWS" | wc -w | tr -d ' ') gateways set to Ignition $want"

  echo
  say "nothing has changed yet -- a running container keeps the image it was
    created from. To take it:"
  dim "    ./wd down && ./wd up"
  echo
  warn "FORWARD ONLY from here. Once a gateway has started on $want, going back
       needs a rebuild -- take a backup first if this rig is worth keeping:
           ./wd gwbk"
}

# --- entry -------------------------------------------------------------------

FORCE=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

if [ "${#ARGS[@]}" -eq 0 ]; then
  report
  exit 0
fi

if [ "$FORCE" -eq 1 ]; then
  # --force skips ONLY the downgrade guard. The floor and the module check are
  # not opinions about your data, they are statements about what will run.
  highest_seen >/dev/null   # still records what it sees
  : > "$SEEN"
  warn "--force: the downgrade guard is off and the record of what these
       volumes have run has been cleared. The floor and the module check
       still apply."
fi
set_version "${ARGS[0]}"
