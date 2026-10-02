#!/usr/bin/env bash
#
# Is there an update waiting? -- the NOTICE half of self-update.
#
#   scripts/update-check.sh            look, record, print what is waiting
#   scripts/update-check.sh --quiet    look and record, print nothing (a timer)
#   scripts/update-check.sh --show     print what was RECORDED -- no network
#   make update-check                  the same as the first form
#
# WHY THIS IS A SEPARATE SCRIPT FROM self-update.sh, AND NOT ITS --check.
# They answer to different callers and must not share a cost. `self-update.sh
# --check` talks to GitHub, needs the ssh key, and therefore only runs on the
# host -- fine for a nightly timer, useless for a login banner or for a line in
# `make status`, both of which have to be instant, offline, and work inside the
# toolbox where there is no key at all.
#
# So the work is split by WHAT IT COSTS, not by what it is about:
#
#   LOOK (--quiet, on a timer)   fetch, compare, write .update-state
#   SHOW (--show, everywhere)    read .update-state and print. No git, no
#                                network, no credential, ~1ms, safe in the
#                                toolbox and safe in a shell profile.
#
# Every surface that notifies you -- the login banner, `make status`,
# `make verify-demos` -- calls SHOW. Exactly one thing calls LOOK. Put a fetch
# in a shell profile and every new terminal blocks on the network, which is how
# a helpful banner becomes the reason the machine feels broken on a bad link.
#
# NOTHING HERE EVER APPLIES AN UPDATE. It reports; you run `make update`. That
# is the whole point of the split -- see docs/SELF-UPDATE.md for the other
# arrangement, where a timer applies it and nobody is told.
#
# --show EXITS 0 WHATEVER IT FINDS, deliberately. It is embedded in `make
# status` and in a shell profile, both of which run under `set -e` somewhere,
# and "there is no update" is the ordinary answer -- a non-zero exit for the
# ordinary answer is the bug this repo keeps writing (see CLAUDE.md). For the
# same reason every conditional below is an `if` rather than `cond && cmd`:
# as a whole statement that form takes its exit status from a false condition,
# and under `set -e` the script then dies at the point it had just succeeded.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

STATE="$REPO_ROOT/.update-state"
# A check that quietly stopped working looks exactly like a machine that is up
# to date. So the age is recorded and shown, and past this it says so out loud
# -- the same reasoning as the readiness snapshot's age on the Demos page.
STALE_DAYS=7

MODE=look
QUIET=0
case "${1:-}" in
  --show)  MODE=show ;;
  --quiet) QUIET=1 ;;
  '')      ;;
  *)       die "usage: update-check.sh [--quiet|--show]" ;;
esac

# --- the record --------------------------------------------------------------
# Flat KEY=VALUE, then `--`, then the commit list -- so a human can cat it and
# a shell can read it without a parser. PARSED, never sourced: the log block is
# built from commit subjects, and sourcing that is a way to execute one.
state_get() {  # state_get <KEY>
  if [ -f "$STATE" ]; then
    sed -n "s/^$1=//p" "$STATE" | head -1
  fi
}
state_log() {
  if [ -f "$STATE" ]; then
    sed -n '/^--$/,$p' "$STATE" | sed '1d'
  fi
}

human_age() {  # human_age <epoch>
  local at="${1:-}" secs
  if [ -z "$at" ]; then printf 'never'; return 0; fi
  secs=$(( $(date +%s) - at ))
  if   [ "$secs" -lt 3600 ];   then printf '%dm ago' $(( secs / 60 ))
  elif [ "$secs" -lt 172800 ]; then printf '%dh ago' $(( secs / 3600 ))
  else                              printf '%dd ago' $(( secs / 86400 ))
  fi
}

show() {
  local behind at age plural have latest
  # Never checked -- say nothing at all. An unconfigured machine must not nag;
  # docs/SELF-UPDATE.md is where you opt in.
  if [ ! -f "$STATE" ]; then return 0; fi

  behind="$(state_get BEHIND)"; behind="${behind:-0}"
  at="$(state_get CHECKED_AT)"
  age="$(human_age "$at")"
  have="$(state_get HAVE)"
  latest="$(state_get LATEST)"

  case "$behind" in ''|*[!0-9]*) behind=0 ;; esac

  if [ "$behind" -gt 0 ]; then
    plural=s; if [ "$behind" -eq 1 ]; then plural=; fi
    # RELEASES, not commits. `3 updates waiting` over a commit count told you
    # how much had been pushed, which is not a decision anybody can act on --
    # half of it may be one change in progress. A release is a tree somebody
    # checked, so this counts those, and names the one at the far end.
    if [ -n "$latest" ] && [ -n "$have" ]; then
      printf '\n%s  ↓ %s release%s waiting: %s → %s%s  %s(checked %s)%s\n' \
        "$C_YEL$C_BOLD" "$behind" "$plural" "$have" "$latest" "$C_RESET" \
        "$C_DIM" "$age" "$C_RESET"
    else
      printf '\n%s  ↓ %s update%s waiting%s  %s(checked %s)%s\n' \
        "$C_YEL$C_BOLD" "$behind" "$plural" "$C_RESET" "$C_DIM" "$age" "$C_RESET"
    fi
    # `| head` SIGPIPEs its upstream, and under pipefail that is a 141 the
    # caller would die on. See CLAUDE.md on grep -q -- same shape.
    state_log | head -8 | sed 's/^/      /' || true
    if [ "$behind" -gt 8 ]; then dim "      ... and $(( behind - 8 )) more"; fi
    # REPO_ROOT is `/work` inside the toolbox, and telling somebody to cd there
    # is telling them to cd into a container that will be gone by the time they
    # read it. `make status` and `make verify-demos` BOTH normally run in the
    # toolbox, so this is the common case rather than the odd one -- and the
    # update has to be taken on the host regardless, because that is where the
    # ssh key is.
    if [ "${WD_TOOLBOX:-}" = "1" ]; then
      printf '      %stake it on the HOST%s (not in here):  make update\n\n' \
        "$C_BOLD" "$C_RESET"
    else
      printf '      %stake it:%s  cd %s && make update\n\n' \
        "$C_BOLD" "$C_RESET" "$REPO_ROOT"
    fi
    return 0
  fi

  # Up to date. The only thing worth saying then is that the CHECK itself has
  # stopped happening -- otherwise this is silent, because a line saying
  # "nothing to do" on every login is a line nobody reads by the third day.
  if [ -n "$(state_get FETCH_FAILED)" ]; then
    dim "  update check could not reach GitHub (last managed $age)"
  elif [ -n "$at" ] && [ $(( ( $(date +%s) - at ) / 86400 )) -ge "$STALE_DAYS" ]; then
    dim "  update check last ran $age -- is wd-update-check.timer enabled?"
  fi
  return 0
}

if [ "$MODE" = show ]; then show; exit 0; fi

# --- looking -----------------------------------------------------------------
# From here we need git and a credential, which means the host. The same
# exception self-update.sh and hosts-setup.sh are, for the same reason: the key
# lives in the host user's ~/.ssh and must not be mounted into the container
# every script runs inside.
if [ "${WD_TOOLBOX:-}" = "1" ]; then
  die "update-check runs on the HOST, not inside the toolbox -- the fetch needs
     your ssh key. In here, only the cached answer is available:

         scripts/update-check.sh --show"
fi

cd "$REPO_ROOT"
branch="$(git rev-parse --abbrev-ref HEAD)"
local_sha="$(git rev-parse HEAD)"
have="$(stack_version_number)"

# Every release tag, oldest first. `sort -V` rather than git's own --sort=v:
# refname, which is not in every git this repo has to run under -- and the `v`
# prefix is stripped and re-added so 1.10.0 sorts after 1.9.0.
release_tags() {
  git tag -l 'v[0-9]*' | sed 's/^v//' | sort -V | sed 's/^/v/' || true
  return 0
}

newest_tag() { release_tags | tail -1; return 0; }

# The tags NEWER than what this machine has, oldest first -- so "several
# releases behind" is a list, in the order they would be taken, rather than one
# number. A machine three releases behind wants to know what is in each.
tags_after() {  # tags_after <version-number|"">
  local seen=0 t
  if [ -z "$1" ]; then release_tags; return 0; fi
  for t in $(release_tags); do
    if [ "$seen" = 1 ]; then printf '%s\n' "$t"; fi
    if [ "$t" = "v$1" ]; then seen=1; fi
  done
  # An unknown version (a tag this clone has not fetched, or a hand-edited
  # VERSION) means we cannot place the machine in the sequence at all. Saying
  # "everything is newer" would be a confident lie, so say nothing.
  if [ "$seen" = 0 ]; then return 0; fi
  return 0
}

# One plain line per release: which areas it changed, and how many entries.
# Built from that tag's OWN CHANGELOG.md, which is the only description of a
# release that travels with it -- and it is already grouped by demonstration,
# so the areas are exactly the words somebody wants.
release_summary() {  # release_summary <tag>
  local body areas n
  body="$(git show "$1:CHANGELOG.md" 2>/dev/null | awk -v tag="## $1 " '
    index($0, tag) == 1 { on = 1; next }
    on && /^## v/ { exit }
    on { print }
  ' || true)"
  if [ -z "$body" ]; then printf 'no changelog section'; return 0; fi
  areas="$(printf '%s\n' "$body" | sed -n 's/^### //p' | paste -sd, - \
           | sed 's/,/, /g' || true)"
  n="$(printf '%s\n' "$body" | grep -c '^- ' || true)"
  printf '%s -- %s change(s)' "${areas:-no areas}" "${n:-0}"
  return 0
}

# Header only. The log block is appended by the caller, because the two paths
# below have different ideas of what the log should say: a successful check
# rebuilds it, a failed one keeps whatever was there.
#
# HAVE/LATEST/BEHIND are about RELEASES; LOCAL/REMOTE/MAIN_AHEAD are about
# commits and are kept because a machine tracking main is a supported thing and
# the banner has to be able to say so.
write_header() {  # write_header <remote-sha> <behind> <fetch-failed|"">
  {
    printf 'CHECKED=%s\n'    "$(date '+%Y-%m-%dT%H:%M:%S%z')"
    printf 'CHECKED_AT=%s\n' "$(date +%s)"
    printf 'BRANCH=%s\n'     "$branch"
    printf 'LOCAL=%.7s\n'    "$local_sha"
    printf 'REMOTE=%.7s\n'   "$1"
    printf 'BEHIND=%s\n'     "$2"
    printf 'HAVE=%s\n'       "${have:+v$have}"
    printf 'LATEST=%s\n'     "$(newest_tag)"
    printf 'MAIN_AHEAD=%s\n' "$(stack_version_ahead)"
    printf 'TRACK=%s\n'      "$([ "$(stack_version_ahead)" -gt 0 ] && echo main || echo tag)"
    if [ -n "$3" ]; then printf 'FETCH_FAILED=%s\n' "$3"; fi
    printf -- '--\n'
  } > "$STATE.tmp"
}

# A dead link is not a failure of this machine and must not fail a timer. It is
# recorded and shown, and the PREVIOUS answer is kept -- "3 updates waiting,
# checked 2 days ago" is more use to somebody about to demonstrate than
# silence is.
#
# --tags, because the tags ARE the releases now. Without it a machine would
# fetch the commits of three releases and know about none of them.
if ! git fetch --quiet --tags origin "$branch" 2>/dev/null; then
  behind="$(state_get BEHIND)"; behind="${behind:-0}"
  remote="$(state_get REMOTE)"; remote="${remote:-$local_sha}"
  old_log="$(state_log)"
  write_header "$remote" "$behind" yes
  if [ -n "$old_log" ]; then printf '%s\n' "$old_log" >> "$STATE.tmp"; fi
  mv "$STATE.tmp" "$STATE"
  if [ "$QUIET" -eq 1 ]; then exit 0; fi
  warn "could not reach GitHub -- showing the last answer instead"
  show
  exit 0
fi

remote_sha="$(git rev-parse "origin/$branch")"

# BEHIND IS COUNTED IN RELEASES. A machine several releases behind gets the
# whole ordered list, each with what it changed, because "take three releases"
# is a different decision from "take one" and the difference is what is in them.
newer="$(tags_after "$have")"
behind=0
if [ -n "$newer" ]; then
  behind="$(printf '%s\n' "$newer" | wc -l | tr -d ' ')"
fi

write_header "$remote_sha" "$behind" ''
for t in $newer; do
  printf '%s  %s\n' "$t" "$(release_summary "$t")" >> "$STATE.tmp"
done
mv "$STATE.tmp" "$STATE"

if [ "$QUIET" -eq 1 ]; then exit 0; fi

latest="$(newest_tag)"
ahead_of_tag="$(stack_version_ahead)"

if [ "$behind" -eq 0 ]; then
  if [ -z "$latest" ]; then
    # No tags at all -- an unreleased repo. Fall back to commits, which is the
    # only thing there is to compare, and say why.
    n="$(git rev-list --count "$local_sha..$remote_sha" || echo 0)"
    if [ "$n" -gt 0 ]; then
      warn "origin/$branch has $n commit(s) this checkout has not, and there are
     no release tags yet -- nothing to track but main. See docs/RELEASING.md."
    else
      ok "up to date with origin/$branch ($(printf '%.7s' "$local_sha")) -- no releases cut yet"
    fi
  elif [ "$ahead_of_tag" -gt 0 ]; then
    # THE DEV VM. It is on main, past the newest release, which is exactly what
    # this machine is for -- so this is a statement, not a warning.
    ok "on the newest release ($latest) and $ahead_of_tag commit(s) past it on $branch"
  else
    ok "on the newest release ($latest)"
  fi
  exit 0
fi
show
