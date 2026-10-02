#!/usr/bin/env bash
# Shared helpers for the scripts in migrations/. Source this, do not execute it.
#
#   . "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"
#
# WHAT A MIGRATION IS FOR. A release is the repo at a tag, and `git checkout` is
# the whole of it -- for files. It is none of it for the things this repo does
# not own: a Docker network, a container's network membership, a generated
# secret, a module installed into a gateway volume, a trigger in Postgres,
# something a retired stack left behind. Those are MACHINE state, and CLAUDE.md
# rule 4 says the gateway is the source of truth for them. A migration is the
# one small script that brings a machine's state up to what a release assumes.
#
# THE THREE RULES A MIGRATION KEEPS:
#
#   1. SAFE TO RUN TWICE. It checks its own end state first and does nothing if
#      it is already right. `scripts/migrate.sh` is wired into self-update and a
#      timer will run it; a migration that is only correct the first time is a
#      migration that breaks the machine on the second.
#   2. SAFE ON A MACHINE THAT NEVER HAD THE OLD STATE. A fresh bootstrap builds
#      everything from scratch, so every migration is already satisfied there --
#      and must say so quietly rather than failing or "repairing" something that
#      was never wrong.
#   3. IT NEVER RESTARTS A GATEWAY BEHIND YOUR BACK. Some steps genuinely need
#      one (installing a module is the example -- it stops the gateway and then
#      raises a certificate gate a human has to clear). Those DEFER: they print
#      the command and stop, and the migration stays pending so it is offered
#      again. A demonstration interrupted by an automatic restart is worse than
#      a machine that is one step behind and says so.
#
# EXIT CODES, which scripts/migrate.sh reads:
#
#   0   done, or already right -- record it as applied
#   2   not finished: something is left for a human (or, under --check, there is
#       work to do). NOT recorded, so it is offered again next time.
#   1   failed. The runner stops: later migrations may depend on this one.

# --- the mode -----------------------------------------------------------------
# --check sets this. A migration in check mode must not write ANYTHING: it says
# what it would do and exits 2 if that is more than nothing.
MIG_CHECK="${WD_MIGRATE_CHECK:-0}"
MIG_WORK=0
MIG_DEFER=0
MIG_NAME="$(basename "${BASH_SOURCE[1]:-$0}")"

# Already right. Says so at `ok` volume, because "nothing to do" IS the answer
# on most machines most of the time and a run that prints nothing reads as a run
# that did not happen.
mig_ok() { ok "$*"; }

# Do a thing, unless we are only looking.
mig_do() {  # mig_do <what it is> <command...>
  local what="$1"; shift
  MIG_WORK=$((MIG_WORK + 1))
  if [ "$MIG_CHECK" = 1 ]; then
    printf '%s  would%s %s\n' "$C_YEL" "$C_RESET" "$what"
    return 0
  fi
  say "$what"
  "$@"
}

# Leave it for a human, and say exactly what they should run. Used for anything
# that stops a gateway or needs sudo -- see rule 3 above.
mig_defer() {  # mig_defer <why> <the command to run>
  MIG_DEFER=$((MIG_DEFER + 1))
  warn "$MIG_NAME: $1"
  printf '%s       run: %s%s\n' "$C_DIM" "$2" "$C_RESET" >&2
}

# Last line of every migration.
mig_finish() {
  if [ "$MIG_DEFER" -gt 0 ]; then exit 2; fi
  if [ "$MIG_CHECK" = 1 ] && [ "$MIG_WORK" -gt 0 ]; then exit 2; fi
  exit 0
}

# --- things several migrations need -------------------------------------------

# THE FRONT DOOR, for the same reason self-update.sh uses it: a migration runs on
# the HOST (it needs docker, and the pull that led here needed an ssh key), but
# the repo's own scripts expect the toolbox -- node, python3, a modern bash.
# Verified 21/09/2026: `scripts/ign-secrets.sh --list` run natively on this host
# exits 2 with no message at all, because ign-gw.js cannot run there; through
# ./wd it prints the provider and its four secret names. So: docker commands
# direct, repo scripts through here.
WD="$REPO_ROOT/wd"

# Does a container exist at all (running or not)?
container_exists() {
  [ -n "$(docker ps -aq --filter "name=^${1}$" 2>/dev/null || true)" ]
}

volume_exists() {
  docker volume inspect "$1" >/dev/null 2>&1
}

network_has() {  # network_has <network> <container>
  local nets
  nets="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
          "$2" 2>/dev/null || true)"
  case " $nets " in *" $1 "*) return 0 ;; esac
  return 1
}
