#!/usr/bin/env bash
#
# The login banner: what a new shell on a demo machine should be told.
#
#   scripts/login-notice.sh --install     add it to this user's ~/.bashrc
#   scripts/login-notice.sh --remove      take it out again
#   scripts/login-notice.sh               print it (what the banner runs)
#
# WHY A BANNER AT ALL. `make update` is a command you have to remember to run,
# and the thing it guards against -- a demo machine drifting weeks behind main
# -- is by definition something nobody noticed. A notice you cannot miss on a
# machine you only visit occasionally is the whole mechanism; the command is
# the easy half.
#
# THE BANNER MUST NOT TOUCH THE NETWORK. It runs on every new terminal, so a
# fetch here means every shell on a bad link hangs before its first prompt, and
# the fix people reach for is deleting the banner. So this prints the answer a
# timer already recorded (scripts/update-check.sh --show): a file read, no git,
# no credential, silent when there is nothing waiting.
#
# INTERACTIVE SHELLS ONLY. ~/.bashrc is sourced by things that are not people
# -- scp and rsync among them, which abort outright if the far end writes
# anything to stdout on connect. The guard below is what stops a helpful notice
# from breaking file copies to this machine, and it is not hypothetical: it is
# the classic way a .bashrc echo bites.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MARK_BEGIN='# --- Ignition-Demos-Stack login notice (scripts/login-notice.sh) ---'
MARK_END='# --- end Ignition-Demos-Stack login notice ---'

# THE MARKER IS AN IDENTITY, NOT A LABEL, so renaming it is a migration.
# `installed()` and `--remove` both match it EXACTLY: change the string alone
# and every block already in a ~/.bashrc becomes invisible to this script --
# `--install` appends a second banner and `--remove` reports nothing to do
# while leaving the first one printing forever. The repo was renamed from
# Work-Dockers on 27/08/2026, so both spellings are honoured: the new one is
# what gets written, the old one is still found and cleaned up.
LEGACY_BEGIN='# --- Work-Dockers login notice (scripts/login-notice.sh) ---'
LEGACY_END='# --- end Work-Dockers login notice ---'
RC="$HOME/.bashrc"

snippet() {
  cat <<EOF
$MARK_BEGIN
# Prints only when an update is waiting. Reads a file -- no network. The
# fetch that fills that file is wd-update-check.timer; see docs/SELF-UPDATE.md.
case \$- in *i*)
  [ -x "$REPO_ROOT/scripts/login-notice.sh" ] && "$REPO_ROOT/scripts/login-notice.sh"
  ;;
esac
$MARK_END
EOF
}

# A grep that finds nothing is the normal answer on a first install, so it ends
# `|| true` and the COUNT is what decides -- never `grep -q`, whose early exit
# SIGPIPEs its upstream and returns 141 under pipefail (see CLAUDE.md).
installed() {
  local n=0
  if [ -f "$RC" ]; then
    n="$(grep -cF "${1:-$MARK_BEGIN}" "$RC" || true)"
  fi
  [ "${n:-0}" -gt 0 ]
}

# Drop the lines from $1 to $2 inclusive. sed -i is not portable (macOS wants an
# argument), and this may run on a Mac. Write beside it and move, which is
# atomic anyway.
strip_block() {
  awk -v b="$1" -v e="$2" '
    $0 == b { skip = 1 } !skip { print } $0 == e { skip = 0 }
  ' "$RC" > "$RC.wd-tmp"
  mv "$RC.wd-tmp" "$RC"
}

case "${1:-}" in
  --install)
    if installed; then
      ok "already installed in $RC"
    elif installed "$LEGACY_BEGIN"; then
      strip_block "$LEGACY_BEGIN" "$LEGACY_END"
      { echo; snippet; } >> "$RC"
      ok "renamed the Work-Dockers block in $RC -- one banner, not two"
    else
      { echo; snippet; } >> "$RC"
      ok "installed in $RC -- open a new terminal to see it"
    fi
    dim "  it prints only when an update is waiting, and only in an interactive shell"
    ;;

  --remove)
    if ! installed && ! installed "$LEGACY_BEGIN"; then
      ok "not installed in $RC -- nothing to do"
      exit 0
    fi
    # `installed && strip_block ...` would be the obvious spelling and is the
    # bug this repo keeps writing: under `set -e` the whole statement's status
    # is the failing test's, so a ~/.bashrc carrying ONLY the legacy block would
    # exit here -- before removing it -- having reported nothing. See CLAUDE.md.
    if installed; then strip_block "$MARK_BEGIN" "$MARK_END"; fi
    if installed "$LEGACY_BEGIN"; then strip_block "$LEGACY_BEGIN" "$LEGACY_END"; fi
    ok "removed from $RC"
    ;;

  '')
    # What the banner itself runs. Silent unless there is something to say.
    #
    # NO VERSION LINE HERE, deliberately -- and it was tried. The version
    # belongs on every surface somebody consults ON PURPOSE (`make status`,
    # `verify-demos`, the console chip), and on none that greets them whether
    # they asked or not: a line on every login is a line nobody reads by the
    # third day, which is this file's whole argument. When there IS something to
    # say the notice now names both versions -- `v1.0.0 → v1.1.0`.
    "$REPO_ROOT/scripts/update-check.sh" --show || true
    ;;

  *)
    die "usage: login-notice.sh [--install|--remove]"
    ;;
esac
