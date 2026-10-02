#!/usr/bin/env bash
#
# Bring THIS MACHINE up to what the release in the tree assumes.
#
#   scripts/migrate.sh             apply everything pending, in order
#   scripts/migrate.sh --check     say what would happen, change nothing
#   scripts/migrate.sh --list      what has been applied, and what is pending
#   scripts/migrate.sh --baseline  record everything as applied WITHOUT running it
#   scripts/migrate.sh --force     re-run everything (they are all idempotent)
#   make migrate / make migrate CHECK=1
#
# `make update` runs this for you, between the pull and the deploy. You would
# run it by hand after a `git checkout` of a tag, or to re-offer a step that
# deferred.
#
# WHY THIS EXISTS. `git checkout v1.2.0` moves every file, and files are not the
# whole of a release. The `wd-mqtt` network does not exist because a compose
# file mentions it; a container created last week is not on a network added this
# week; a generated secret is not in git by design; a module lives in a gateway's
# Docker volume; a Postgres trigger lives in Postgres. Until this script,
# self-update reported those as "stacks/ changed -- NOT applied" and left them,
# which is right for a gateway restart and wrong for everything else -- so a
# machine could be current in git and broken in fact, with nothing to say so.
#
# WHAT IS RECORDED, AND WHY IT IS PER-FILE. `.migrations-applied` lists one line
# per migration that has run: `<version>|<file>|<when>`. The obvious alternative
# -- a single "applied up to 1.2.0" marker -- fails the first time a migration
# is BACKFILLED, which is exactly how this scheme started: six migrations
# numbered 1.0.0 were written after the changes they cover had already been
# pushed. A version marker would have skipped every one of them on a machine
# that had already taken those commits. A per-file record cannot.
#
# It sits beside `.update-applied` (what self-update last deployed) and
# `.update-state` (what update-check last saw), is gitignored for the same
# reason those are -- per-machine, derived, and a tracked file a timer rewrites
# would make self-update's dirty-tree guard refuse every update.
#
# ORDER. Filenames are `<version>-<NN>-<slug>.sh`; sorted by version, then by
# the rest of the name, so NN orders the migrations WITHIN a release where one
# has to come before another (the MQTT network before anything that dials over
# it). `<version>-<slug>.sh` with no NN is accepted too and sorts after nothing
# in particular -- use NN when order matters.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MIGRATIONS_DIR="$REPO_ROOT/migrations"
APPLIED="$REPO_ROOT/.migrations-applied"

MODE=apply
case "${1:-}" in
  --check)    MODE=check ;;
  --list)     MODE=list ;;
  --baseline) MODE=baseline ;;
  --force)    MODE=force ;;
  '')         ;;
  *) die "usage: migrate.sh [--check|--list|--baseline|--force]" ;;
esac

# --- the record ---------------------------------------------------------------

is_applied() {  # is_applied <file>
  [ -f "$APPLIED" ] || return 1
  local n
  n="$(grep -cE "^[^|]*\|$1\|" "$APPLIED" 2>/dev/null || true)"
  [ "${n:-0}" -gt 0 ]
}

record() {  # record <version> <file>
  printf '%s|%s|%s\n' "$1" "$2" "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$APPLIED"
}

version_of() {  # version_of <file>   -- the leading x.y.z
  printf '%s' "$1" | sed -n 's/^\([0-9]\+\.[0-9]\+\.[0-9]\+\).*/\1/p'
}

# Every migration, in order. `sort -t- -k1,1V` would be neat and is not portable
# enough (BSD sort has no -V); sorting the version and the remainder separately
# with `sort -V` on the whole filename gives the same answer here because the
# version is a fixed-shape prefix.
#
# The `|| true` and the -f test: an empty migrations/ is a legitimate state (a
# release that needed no machine-side work), and an unmatched glob stays literal.
all_migrations() {
  local f
  for f in "$MIGRATIONS_DIR"/*.sh; do
    [ -f "$f" ] || continue
    printf '%s\n' "$(basename "$f")"
  done | sort -V
  return 0
}

pending_migrations() {
  local f
  for f in $(all_migrations); do
    if [ "$MODE" = force ] || ! is_applied "$f"; then printf '%s\n' "$f"; fi
  done
  return 0
}

# --- list ---------------------------------------------------------------------

if [ "$MODE" = list ]; then
  say "migrations ($(all_migrations | wc -l | tr -d ' ') in $MIGRATIONS_DIR)"
  for f in $(all_migrations); do
    if is_applied "$f"; then
      printf '%s  applied%s  %s  %s%s%s\n' "$C_GRN" "$C_RESET" "$f" \
        "$C_DIM" "$(sed -n "s/^[^|]*|$f|//p" "$APPLIED" | head -1)" "$C_RESET"
    else
      printf '%s  PENDING%s  %s\n' "$C_YEL$C_BOLD" "$C_RESET" "$f"
    fi
  done
  exit 0
fi

# --- baseline -----------------------------------------------------------------
#
# For a machine that has just BOOTSTRAPPED. Bootstrap builds every one of these
# end states from nothing -- the network, the secrets, the modules, the guard --
# so running the migrations there would be work to reach a state already
# reached. They would all no-op correctly; this just says so without spending a
# minute proving it, and leaves a record so the first real migration after this
# release is the only one that runs.
if [ "$MODE" = baseline ]; then
  n=0
  for f in $(all_migrations); do
    if is_applied "$f"; then continue; fi
    record "$(version_of "$f")" "$f"
    n=$((n + 1))
  done
  if [ "$n" -gt 0 ]; then
    ok "recorded $n migration(s) as applied without running them (fresh build)"
  else
    ok "every migration was already recorded"
  fi
  exit 0
fi

# --- going backwards ----------------------------------------------------------
#
# A DOWNGRADE MOVES THE FILES AND NOTHING ELSE. `git checkout v1.1.0` takes the
# tree back; it does not un-install a module, un-create a network, un-write a
# Postgres trigger or bring back a container that was removed. There is no
# `--down` here and there will not be one: a reverse migration is a second body
# of code that is exercised approximately never and is therefore wrong when it
# finally runs.
#
# What this can do honestly is SAY SO, and name which of the applied migrations
# cannot be undone. Each migration declares that itself, in its own header, as
#     # ONE-WAY: <what cannot be undone>
# so the list is next to the code that caused it rather than in a table that
# goes stale. Migrations for versions newer than this tree are looked up in the
# record, because their files are not here any more.
one_way_warning() {
  local ver f line found=""
  ver="$(stack_version_number)"
  [ -n "$ver" ] || return 0
  [ -f "$APPLIED" ] || return 0
  while IFS='|' read -r aver af _; do
    [ -n "${af:-}" ] || continue
    # Newer than the tree we are now on?
    [ "$(printf '%s\n%s\n' "$ver" "$aver" | sort -V | tail -1)" = "$aver" ] || continue
    [ "$aver" != "$ver" ] || continue
    f="$MIGRATIONS_DIR/$af"
    line=""
    if [ -f "$f" ]; then
      line="$(sed -n 's/^# ONE-WAY: //p' "$f" | head -1 || true)"
    fi
    found="$found
  $af  ${line:-(its file is not in this tree, so what it did cannot be read here)}"
  done < "$APPLIED"
  if [ -n "$found" ]; then
    warn "this tree is v$ver, but migrations from LATER releases have already run here.
       The code has gone back; these have not, and cannot:$found

       docs/RELEASING.md, 'Going back', says what to do about each."
  fi
  return 0
}
one_way_warning

# --- apply --------------------------------------------------------------------

PENDING="$(pending_migrations)"
if [ -z "$PENDING" ]; then
  ok "no migrations pending -- this machine matches $(stack_version)"
  exit 0
fi

n_pending="$(printf '%s\n' "$PENDING" | wc -l | tr -d ' ')"
if [ "$MODE" = check ]; then
  say "$n_pending migration(s) pending -- looking only, nothing will be written"
else
  say "$n_pending migration(s) pending"
fi

need_docker

DONE=""
DEFERRED=""
for f in $PENDING; do
  ver="$(version_of "$f")"
  echo
  printf '%s--- %s%s\n' "$C_BOLD" "$f" "$C_RESET"

  rc=0
  if [ "$MODE" = check ]; then
    WD_MIGRATE_CHECK=1 bash "$MIGRATIONS_DIR/$f" || rc=$?
  else
    bash "$MIGRATIONS_DIR/$f" || rc=$?
  fi

  case "$rc" in
    0)
      if [ "$MODE" = check ]; then
        dim "  nothing to do"
      else
        # RECORDED ONLY ON A CLEAN 0, and only after the script returned. The
        # same discipline self-update.sh applies to .update-applied: a marker
        # written before the work is a marker that lies, and it lies in the
        # direction that hides the failure.
        if ! is_applied "$f"; then record "$ver" "$f"; fi
        DONE="$DONE $f"
      fi
      ;;
    2)
      DEFERRED="$DEFERRED $f"
      ;;
    *)
      die "$f failed (exit $rc) -- nothing after it was run.
     Later migrations can depend on earlier ones, so this stops here. Fix what
     it reported and re-run: scripts/migrate.sh"
      ;;
  esac
done

echo
if [ "$MODE" = check ]; then
  if [ -n "$DEFERRED" ]; then
    say "--check: work is waiting --$DEFERRED"
  else
    ok "--check: every pending migration is already satisfied on this machine"
  fi
  exit 0
fi

if [ -n "$DONE" ]; then ok "applied:$DONE"; fi
if [ -n "$DEFERRED" ]; then
  # NOT an error. A deferred migration is one that needs a human to pick a
  # moment -- a gateway restart, a sudo -- and failing the whole update for that
  # would mean a timer that reports failure every night for a machine that is
  # working perfectly. It stays pending, so it is offered again.
  warn "still pending, each needing something a script should not decide:$DEFERRED"
  dim "     the lines above say what to run. Then: scripts/migrate.sh"
fi
exit 0
