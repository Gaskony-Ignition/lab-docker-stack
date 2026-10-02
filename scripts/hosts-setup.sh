#!/usr/bin/env bash
#
# Point the stack's .test names at the local Nginx Proxy Manager.
#
# THIS RUNS ON THE HOST, NOT IN THE TOOLBOX -- it is the one setup step that
# cannot: the hosts file belongs to the machine, and the toolbox is a container
# with its own. `wd` and `wd.cmd` call it before starting the container, which
# is also where the `backbone` network and the image get created for the same
# reason.
#
# Without it the whole proxy layer is unreachable by name. Every service still
# answers on its host port, so the stack looks completely healthy while
# `https://ignition.test` fails to resolve -- and on a redundant pair that is
# not a cosmetic loss: ignition.test IS the failover front door, so a session
# cannot follow the surviving half without it.
#
#   scripts/hosts-setup.sh            add whatever is missing (may prompt for sudo)
#   scripts/hosts-setup.sh --check    exit 0 if every name is present, 1 if not
#   scripts/hosts-setup.sh --prune    also REMOVE .test names no stack declares
#
# Idempotent: it adds only the names that are absent, and never rewrites a line
# somebody else put there.
#
# REMOVING IS OPT-IN, AND THAT IS DELIBERATE. Retiring a stack leaves its name
# resolving to the proxy, which then serves the down-page for a demo that no
# longer exists -- so a stale name has to be findable, and every run reports
# one. But `wd` calls this on ordinary invocations, and a script that silently
# deletes lines from /etc/hosts every time somebody runs `./wd status` is a
# worse problem than the one it solves. So: always REPORT, only ever delete
# when asked. `--check` reports them too and still exits 0 for them, because a
# stale name breaks nothing -- it is untidy, not broken.
#
# What it will delete is narrow on purpose: a line mapping exactly one
# hostname, that hostname ending in `.test`, and no stack.meta declaring it.
# Anything with a comment, several names, or a name outside .test is left
# alone -- this file belongs to the machine, not to this repo.
#
# NPM keeps its own copy of the same list and has the same gap; see
# stacks/npm/create-proxy-hosts.sh --prune.
set -eu

MARKER='# Ignition-Demos-Stack .test names -- added by scripts/hosts-setup.sh'
TARGET_IP=127.0.0.1

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Derived from the stack manifests, so this can never drift from the proxy
# table again -- both read the same TEST_HOST lines. The grammar (enforced by
# `make validate`) guarantees bare, non-empty, CR-free values.
NAMES="$(sed -n 's/^TEST_HOST=//p' "$REPO"/stacks/*/stack.meta | tr -d '\r' | tr '\n' ' ')"
[ -n "$NAMES" ] || { echo "hosts-setup: no stack.meta files under $REPO/stacks" >&2; exit 1; }

# Git Bash on Windows sees the real hosts file through /c/, and $WINDIR is set
# there and nowhere else, which is the cheapest way to tell the three apart.
if [ -n "${WINDIR:-}" ]; then
  HOSTS="$(printf '%s' "$WINDIR" | sed 's|\\|/|g; s|^\([A-Za-z]\):|/\L\1|')/System32/drivers/etc/hosts"
else
  HOSTS=/etc/hosts
fi

[ -f "$HOSTS" ] || { echo "hosts-setup: no hosts file at $HOSTS" >&2; exit 1; }

missing=''
for n in $NAMES; do
  # Match the NAME as a whole word on a non-comment line: a substring match
  # would see edge1.test inside a comment, or ignition.test inside
  # ignition.test.example.com, and skip a name that is not actually mapped.
  grep -qiE "^[^#]*[[:space:]]$n([[:space:]]|$)" "$HOSTS" || missing="$missing $n"
done

# --- names in the file that no stack declares any more ------------------------
# awk rather than a grep loop: this has to consider every line's shape, not ask
# a yes/no question per known name. $1 is the address and $2 the only hostname,
# NF==2 rejects a multi-name line, and a leading # rejects a comment.
stale="$(awk -v want=" $NAMES " '
  /^[[:space:]]*#/ { next }
  NF == 2 && $2 ~ /\.test$/ && index(want, " " $2 " ") == 0 { print $2 }
' "$HOSTS" | sort -u | tr '\n' ' ' | sed 's/ *$//')"
[ -z "$stale" ] || echo "hosts-setup: no stack declares these, still in $HOSTS: $stale"

if [ "${1:-}" = "--prune" ] && [ -n "$stale" ]; then
  ptmp="$(mktemp)"
  awk -v want=" $NAMES " '
    /^[[:space:]]*#/ { print; next }
    NF == 2 && $2 ~ /\.test$/ && index(want, " " $2 " ") == 0 { next }
    { print }
  ' "$HOSTS" > "$ptmp"
  if [ -w "$HOSTS" ]; then
    cat "$ptmp" > "$HOSTS"
  elif command -v sudo >/dev/null 2>&1; then
    echo "hosts-setup: removing $stale from $HOSTS (sudo)"
    sudo cp "$ptmp" "$HOSTS"
  else
    rm -f "$ptmp"
    echo "hosts-setup: cannot write $HOSTS and no sudo -- remove by hand: $stale" >&2
    exit 1
  fi
  rm -f "$ptmp"
  echo "hosts-setup: removed $stale"
fi

if [ -z "$missing" ]; then
  [ "${1:-}" = "--check" ] || echo "hosts-setup: all .test names already present in $HOSTS"
  exit 0
fi

if [ "${1:-}" = "--check" ]; then
  echo "hosts-setup: missing from $HOSTS:$missing" >&2
  exit 1
fi

block="$MARKER"
for n in $missing; do
  block="$block
$TARGET_IP $n"
done

# Write through a temp copy and move it into place, rather than appending to the
# live file: a half-written hosts file breaks name resolution for the whole
# machine, including anything needed to fix it.
tmp="$(mktemp)"
cat "$HOSTS" > "$tmp"
printf '\n%s\n' "$block" >> "$tmp"

if [ -w "$HOSTS" ]; then
  cat "$tmp" > "$HOSTS"
elif command -v sudo >/dev/null 2>&1; then
  echo "hosts-setup: adding$missing to $HOSTS (sudo)"
  sudo cp "$tmp" "$HOSTS"
else
  rm -f "$tmp"
  cat >&2 <<EOF
hosts-setup: cannot write $HOSTS and no sudo available.

     Add these lines by hand, then re-run:
$(for n in $missing; do printf '       %s %s\n' "$TARGET_IP" "$n"; done)
EOF
  exit 1
fi
rm -f "$tmp"

echo "hosts-setup: added$missing -> $TARGET_IP"
