#!/usr/bin/env bash
#
# Pull this repo and apply what changed -- for a machine nobody logs into.
#
#   scripts/self-update.sh                 go to the NEWEST RELEASE, validate, apply
#   scripts/self-update.sh --check         say what would happen, change nothing
#   scripts/self-update.sh --track main    follow main commit by commit instead
#   scripts/self-update.sh --version v1.2.0   go to that exact release
#   make update / make update CHECK=1 / make update TRACK=main / make update VERSION=v1.2.0
#
# IT FOLLOWS TAGS, NOT COMMITS. This used to take whatever was pushed last,
# which meant every machine ran the half of a change whose other half was still
# being written. A tag is a deliberate statement that a tree was checked --
# validate clean, demos verified -- and it is the only thing a machine away from
# here can trust (docs/RELEASING.md). A machine on a release sits on a DETACHED
# HEAD at that tag, which is the honest description of where it is.
#
# `--track main` keeps the old behaviour, for a development machine where the
# person breaking it is the person watching it. `--version` goes to a named
# release, forwards or back.
#
# Written for a demo VM that should stay current without anyone driving it. Run
# it from a systemd timer (see docs/SELF-UPDATE.md) or by hand.
#
# WHAT "UPDATE" MEANS HERE IS NOT "git pull". A pull leaves the gateway running
# exactly what it was running: projects are files the gateway only picks up on a
# scan, and gateway THEMES are config resources that a running gateway does not
# recompile at all. So this pulls, then applies -- and applies only what is
# safe to apply while somebody might be watching.
#
# THREE RULES IT WILL NOT BREAK, because it runs unattended:
#
#   1. NEVER RESTART A GATEWAY. `make deploy` never restarts one, which is why
#      it is safe with sessions open. A changed THEME does need a restart to
#      recompile -- so this installs it, notices it is not live, and SAYS SO
#      rather than deciding for you. A demo interrupted by an automatic restart
#      is a worse outcome than a theme that is one release behind.
#   2. NEVER APPLY A TREE THAT DOES NOT VALIDATE. That is what validate is for,
#      and a machine with nobody at the console is exactly where a broken
#      deploy goes unnoticed.
#   3. NEVER MERGE. `--ff-only`. Local commits or a dirty tree mean somebody
#      was working here, and quietly rebasing over that is unforgivable in a
#      script -- it stops and says what it found.
#
# THIS RUNS ON THE HOST, NOT IN THE TOOLBOX -- the same exception hosts-setup.sh
# is. The pull needs a GIT CREDENTIAL, and the credential is an ssh key in the
# host user's ~/.ssh. The toolbox has no ~/.ssh and must not be given one: a
# private key bind-mounted into a container that every script runs inside is a
# key you can no longer reason about.
#
# Run in the toolbox anyway and git says
#     fatal: could not read Username for 'https://github.com'
# which reads as a broken remote rather than a container with no keys. So the
# guard below names it, and the APPLY steps go back through `./wd` -- the work
# that needs docker and the gateway belongs in the toolbox, the work that needs
# your identity does not.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${WD_TOOLBOX:-}" = "1" ]; then
  die "self-update runs on the HOST, not inside the toolbox.
     The pull needs your ssh key, which lives in the host's ~/.ssh and is
     deliberately not mounted in here.

         scripts/self-update.sh          (not ./wd self-update)
         make update"
fi

CHECK=0
FORCE=0
TRACK=tag
WANT_TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    # Re-apply everything regardless of what this machine has already applied --
    # for a gateway that was rebuilt, or a volume that was destroyed, where the
    # repo is right and the gateway is empty.
    --force) FORCE=1; shift ;;
    --track)
      TRACK="${2:-}"; shift 2
      case "$TRACK" in tag|main) ;; *) die "--track takes 'tag' or 'main'" ;; esac
      ;;
    --version)
      WANT_TAG="${2:-}"; shift 2
      case "$WANT_TAG" in v[0-9]*) ;; *) die "--version takes a tag like v1.2.0 (got '$WANT_TAG')" ;; esac
      ;;
    *) die "usage: self-update.sh [--check] [--force] [--track tag|main] [--version vX.Y.Z]" ;;
  esac
done

cd "$REPO_ROOT"

# Everything that touches docker or the gateway goes through the front door, so
# this works the same on a machine that has no python or node of its own.
WD="$REPO_ROOT/wd"

# ONE AT A TIME. A timer that fires while the last run is still deploying gets
# two `docker cp` streams into the same gateway. flock is in util-linux, which
# -- unlike `sg`, learned the hard way in blank-vm.sh -- really is everywhere.
LOCK="${TMPDIR:-/tmp}/wd-self-update.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { say "another self-update is running -- skipping this tick"; exit 0; }
fi

# --- 1. is there anything to take? -------------------------------------------
dirty="$(git status --porcelain | head -5 || true)"
if [ -n "$dirty" ]; then
  die "the working tree is not clean, so nothing was pulled:

$(printf '%s\n' "$dirty" | sed 's/^/       /')

     Somebody has been working here. Commit or discard, then re-run."
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
before="$(git rev-parse HEAD)"

# --tags: the tags ARE the releases. Without it a machine fetches the commits of
# three releases and knows about none of them.
git fetch --quiet --tags origin main || die "fetch failed -- is the deploy key still valid?
     Test it with: ssh -T git@github.com"

# Every release tag, oldest first. `sort -V` with the `v` stripped and re-added,
# so 1.10.0 sorts after 1.9.0 -- and not git's own --sort=v:refname, which is
# not in every git this repo has to run under.
release_tags() {
  git tag -l 'v[0-9]*' | sed 's/^v//' | sort -V | sed 's/^/v/' || true
  return 0
}

# WHERE ARE WE GOING? Three answers, and the mode decides which.
TARGET_TAG=""
if [ "$TRACK" = main ]; then
  if [ "$branch" != "main" ]; then
    die "--track main, but this checkout is on '$branch'.
     A machine on a release sits on a detached HEAD at its tag, which is not a
     branch to pull. Either take a release (drop --track main), or put the
     checkout back on main by hand first:  git checkout main"
  fi
  after="$(git rev-parse origin/main)"
elif [ -n "$WANT_TAG" ]; then
  git rev-parse -q --verify "refs/tags/$WANT_TAG" >/dev/null 2>&1 \
    || die "there is no release tagged $WANT_TAG.
     What exists:  $(release_tags | tr '\n' ' ')"
  TARGET_TAG="$WANT_TAG"
  after="$(git rev-list -n1 "$TARGET_TAG")"
else
  TARGET_TAG="$(release_tags | tail -1)"
  [ -n "$TARGET_TAG" ] || die "there are no release tags, so there is no release to take.
     Either cut one (make release BUMP=minor -- docs/RELEASING.md), or follow
     main deliberately on this machine:  make update TRACK=main"
  after="$(git rev-list -n1 "$TARGET_TAG")"
fi

# WHAT THE GATEWAY IS RUNNING IS NOT "WHAT THE LAST PULL MOVED". Deriving the
# work from `before..after` assumed this script is the only thing that ever
# moves HEAD, and it is not -- which broke it in two ways, both silent, both
# ending in a repo that is current and a gateway that is not:
#
#   * `git pull` BY HAND, then `make update`. before == after, so it said
#     "already up to date" and deployed nothing -- for ever, because the
#     commits it needed to notice were already behind HEAD. This is the
#     obvious thing to do and it is what actually happened.
#   * THE STACK WAS DOWN during a run. The pull landed, the apply was skipped
#     (correctly -- there is no gateway to deploy to), and the next run
#     computed an empty diff from the new HEAD. Never applied, never mentioned.
#
# So the base is what was last successfully APPLIED, recorded here and nowhere
# else, and written only after the apply actually happens. Judge by end state,
# not by what a command returned -- the same rule bootstrap step 7 follows.
APPLIED_FILE="$REPO_ROOT/.update-applied"
last_applied="$(cat "$APPLIED_FILE" 2>/dev/null || true)"
# A sha recorded by an older checkout may not exist here any more; a base git
# cannot resolve would abort the diff below, so it is treated as no record.
if [ -n "$last_applied" ] && ! git cat-file -e "$last_applied^{commit}" 2>/dev/null; then
  last_applied=''
fi

# An EMPTY base means "no idea what this gateway has seen" -- the first run
# after this change, a fresh clone, or --force. We cannot know, so we apply
# everything once and say so. deploy-all never restarts a gateway, so being
# wrong that way costs a few seconds; being wrong the other way is the bug
# above, and it is invisible.
base="$last_applied"
if [ "$FORCE" -eq 1 ]; then base=''; fi

if [ -n "$base" ] && [ "$base" = "$after" ] && [ "$before" = "$after" ]; then
  ok "already up to date ($(printf '%.7s' "$before")) -- and applied"
  exit 0
fi

# GOING BACK IS A DIFFERENT SENTENCE FROM GOING FORWARD, and must read like
# one. `after` being an ancestor of `before` means a downgrade: the code goes
# back, and nothing machine-side comes back with it (see the migration note
# below, and docs/RELEASING.md).
GOING_BACK=0
if [ "$before" != "$after" ] && git merge-base --is-ancestor "$after" "$before" 2>/dev/null; then
  GOING_BACK=1
fi

from_desc="$(stack_version)"
if [ "$GOING_BACK" -eq 1 ]; then
  warn "GOING BACK: $from_desc -> ${TARGET_TAG:-$(printf '%.7s' "$after")}"
  warn "The files go back. Machine state does NOT come back with them -- a
       migration that has run has run. docs/RELEASING.md says which are
       one-way; scripts/migrate.sh names them before it does anything."
elif [ "$before" != "$after" ]; then
  if [ -n "$TARGET_TAG" ]; then
    say "$from_desc -> $TARGET_TAG ($(git rev-list --count "$before..$after" || echo '?') commit(s)):"
  else
    say "$(git rev-list --count "$before..$after" || echo '?') new commit(s) on main:"
  fi
  git --no-pager log --oneline "$before..$after" | head -10 | sed 's/^/    /' || true
elif [ -n "$base" ] && [ "$base" != "$after" ]; then
  say "repo is current, but $(git rev-list --count "$base..$after" || echo '?') commit(s) were never applied here:"
  git --no-pager log --oneline "$base..$after" | head -10 | sed 's/^/    /' || true
else
  say "no record of what this machine has applied -- applying everything once"
fi

if [ "$CHECK" -eq 1 ]; then
  echo
  say "the migrations this would run:"
  "$REPO_ROOT/scripts/migrate.sh" --check 2>&1 | sed 's/^/    /' || true
  echo
  say "--check: nothing was moved or applied"
  exit 0
fi

if [ "$before" != "$after" ]; then
  if [ "$TRACK" = main ]; then
    git merge --ff-only origin/main \
      || die "not a fast-forward -- there are local commits on main.
     Nothing was applied. Sort the history out by hand."
    ok "pulled to $(printf '%.7s' "$after")"
  else
    # DETACHED, deliberately. "On release v1.2.0" is not a branch, and pretending
    # it is -- by resetting main onto the tag -- would destroy the branch's
    # meaning on any machine that later wants to follow main again.
    git -c advice.detachedHead=false checkout --quiet --detach "$TARGET_TAG" \
      || die "could not check out $TARGET_TAG. Nothing was applied."
    ok "now on release $TARGET_TAG"
  fi
fi


# --- 2. what changed decides what to do --------------------------------------
# A grep that finds nothing is the NORMAL answer for every one of these, so
# each ends `|| true` -- see CLAUDE.md on the bug this repo keeps writing.
if [ -n "$base" ]; then
  changed="$(git diff --name-only "$base..$after" || true)"
else
  # Everything, because we have no idea what this gateway has seen.
  changed="$(git ls-files || true)"
fi
touched() { printf '%s\n' "$changed" | grep -c "^$1" || true; }

n_projects=$(touched "ignition/projects/")
n_themes=$(touched "ignition/themes/")
n_stacks=$(touched "stacks/")
n_scripts=$(touched "scripts/")

# --- 3. refuse to apply a tree that does not validate -------------------------
say "validating the new tree"
if ! "$WD" validate >/dev/null 2>&1; then
  "$WD" validate 2>&1 | tail -20
  die "the pulled tree does NOT validate -- nothing was applied.
     The repo is at $(printf '%.7s' "$after"); the gateway still runs what it ran.
     Fix upstream and this will apply on the next run."
fi
ok "validate clean"

# --- 3b. the machine-side half of the release ---------------------------------
#
# AFTER VALIDATE AND BEFORE THE DEPLOY, both on purpose. After, because rule 2
# applies to machine state at least as much as to projects -- a tree that does
# not validate must not be allowed to reconfigure a broker. Before, because a
# release's migrations bring this machine up to what the new tree ASSUMES (the
# MQTT network, a generated token, a Postgres trigger), and a project deployed
# onto a machine that has not had them runs against a rig that cannot serve it.
#
# Before the hub-is-down exit below, too: most migrations do not need a gateway,
# and the ones that do defer on their own and say what to run.
#
# IT NEVER FAILS THE UPDATE. A migration that needs a human -- a gateway
# restart, a sudo -- defers, stays pending and is offered again. A timer
# reporting failure every night for a machine that is working perfectly is a
# timer whose output nobody reads by the third day.
#
# AND ONE REMINDER FIRST. An engineer is meant to open these gateways and change
# settings -- that is half of what the MQTT demo is for. Some migrations re-run
# the setup scripts, which write those settings back. Updates win, and that is
# the right rule; losing a day's poking about without being told is not. So the
# RECORDED drift answer is printed here (no gateway calls -- `make drift` is what
# looks, this only shows what it last found), and it names the way to keep a copy.
if [ -x "$REPO_ROOT/scripts/drift.sh" ]; then
  "$REPO_ROOT/scripts/drift.sh" --show || true
fi

say "migrations"
"$REPO_ROOT/scripts/migrate.sh" || warn "some migrations did not complete -- see above"

# --- 4. apply, in the order that works ----------------------------------------
applied=""

# Nothing below can work against a gateway that is not up, and a VM with the
# stack deliberately down is the normal resting state -- the pull is still
# worth having, so this is not an error.
if ! docker ps --format '{{.Names}}' | grep -qx ignition; then
  warn "the hub is not running, so nothing was deployed.
       The repo is current; it will apply the next time the stack is up."
  exit 0
fi

if [ "$n_themes" -gt 0 ]; then
  say "themes changed ($n_themes file(s)) -- installing"
  "$WD" -- scripts/ign-themes.sh || warn "theme install reported a problem"
  applied="$applied themes"
fi

if [ "$n_projects" -gt 0 ]; then
  say "projects changed ($n_projects file(s)) -- deploying"
  "$WD" deploy-all
  applied="$applied projects"
fi

# Every run, not only when something changed: this is how a machine built
# before a setting existed gets it (scripts/converge.sh). Restarts nothing.
say "gateway settings -- re-applying what bootstrap sets"
if "$WD" converge; then
  applied="$applied settings"
else
  warn "some gateway settings did not apply -- the lines above name them"
fi

# Deliberately NOT acted on: a changed compose file needs the stack recreated,
# which stops containers. That is a restart by another name, so it is reported
# and left. Same for scripts -- they are picked up on next use by definition.
#
# ONLY WHEN THERE IS A BASE TO HAVE CHANGED FROM. With no record the file list
# is the whole repo, so these two would report "stacks/ changed (36 file(s))"
# on a machine where nothing changed at all -- a true-looking number that is
# not a fact, sending somebody off to recreate stacks for no reason. Nothing
# is skipped by staying quiet here: both lines are advisory, and the run that
# genuinely changes a compose file will have a base by then.
if [ -n "$base" ]; then
  if [ "$n_stacks" -gt 0 ]; then
    # NAME THEM, and separate the gateways from everything else. "stacks/
    # changed" over a `down && up` is true and nearly useless: it asks for the
    # whole rig to be stopped when the change may be one line of proxy config
    # that no gateway would even notice. The distinction that matters is whether
    # a GATEWAY is in the set -- those are the containers a demonstration is
    # living in, and the only ones worth waiting for a quiet moment to recreate.
    #
    # It is not hypothetical: the fix for a front door that was silently serving
    # the wrong container is a config file under stacks/ignition-ha, and left as
    # a blanket `down && up` it would sit unapplied on a machine whose owner is
    # reasonably unwilling to stop everything for it.
    hit="$(printf '%s\n' "$changed" | sed -n 's#^stacks/\([^/]*\)/.*#\1#p' \
           | sort -u | tr '\n' ' ')"
    gw=""; other=""
    for s in $hit; do
      if [ -n "$(meta_get "$s" ROLE "")" ]; then gw="$gw $s"; else other="$other $s"; fi
    done
    warn "stacks/ changed ($n_stacks file(s)) -- NOT applied:$hit"
    if [ -n "$other" ]; then
      dim  "  no gateway in these -- restart them alone, any time:"
      for s in $other; do dim "      ./wd restart STACK=$s"; done
    fi
    if [ -n "$gw" ]; then
      dim  "  these ARE gateways; recreating one stops it. When nobody is watching:"
      for s in $gw; do dim "      ./wd restart STACK=$s"; done
    fi
  fi
  if [ "$n_scripts" -gt 0 ]; then
    say "scripts/ changed ($n_scripts file(s)) -- in effect from the next run"
  fi
fi

# ONLY NOW. The marker is the record that this gateway has seen everything up
# to `after`, so it is written after the apply and nowhere else -- not after
# the pull, and never on the hub-is-down path above, which exits before this.
# Written wrongly it would recreate the exact bug it exists to prevent, one
# release later and much harder to see.
printf '%s\n' "$after" > "$APPLIED_FILE"

echo
if [ -n "$applied" ]; then
  ok "now on $(stack_version) --$applied"
else
  ok "now on $(stack_version) -- nothing needed applying"
fi

# The one thing left that a human has to decide. ign-themes.sh already reports
# which gateways are serving stale CSS; repeat it here as the closing line,
# because that is the only outstanding action and it must not be buried.
if [ "$n_themes" -gt 0 ]; then
  warn "if the theme report above says STALE, a gateway needs restarting to
       recompile it. NOT done automatically -- pick your moment:
           docker restart ignition"
fi

# --- 5. is it still demonstrable? ---------------------------------------------
#
# THE POINT OF THE WHOLE EXERCISE. Everything above is about being current;
# this is the only part that asks whether the machine can still do its job.
# read-only, and it checks the demos this machine has STARTED, so on a rig
# parked at the core it is a few seconds.
echo
say "verify-demos"
if ! "$WD" verify-demos; then
  warn "the stack is current but verify-demos is NOT clean. The lines above name
       the repair for each failure. Nothing here has been rolled back: the tree
       is a release that validated, and what it reports is machine state."
fi
