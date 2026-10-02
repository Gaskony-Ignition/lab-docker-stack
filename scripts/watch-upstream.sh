#!/usr/bin/env bash
#
# Alert when a third-party module we run publishes a new release.
#
#   scripts/watch-upstream.sh             check once; prints drift, exit 1 if any
#   scripts/watch-upstream.sh --watch     poll forever, one line per NEW release
#   scripts/watch-upstream.sh --pin       record what is live now as the pins
#   scripts/watch-upstream.sh --upgrade   fetch the new release, re-hash it and
#                                         rewrite modules.manifest + the pins
#
# Why this exists: the Architecture Builder is a one-person project under active
# development -- ten releases in the five weeks to 22/07/2026 -- and it is a
# self-signed module with no upgrade notification of any kind inside Ignition.
# The gateway will happily keep running 1.1.2 forever while upstream fixes the
# bug you are about to hit. There is no feed to subscribe to, so we poll.
#
# The pins file is COMMITTED, so it doubles as the record of which module
# versions this stack was last verified against. `git diff` after --pin is the
# changelog entry.
#
# Deliberately no GitHub token: these are public repos and the unauthenticated
# limit (60/hour) is far above a 30-minute poll. `gh` is used when present
# because it picks up an existing login and raises that limit for free.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PINS="scripts/upstream.pins"
MANIFEST="scripts/modules.manifest"

# THE IGNITION VERSION IS PART OF THE QUESTION, NOT A DETAIL.
#
# A module built for one Ignition minor will not load on another: the gateway
# logs a version mismatch and carries on without it, which looks exactly like
# the module simply not working. Some upstreams publish PARALLEL channels for
# several Ignition versions -- Embr tags `releases/8.3/<date>` and
# `releases/8.1/<date>` on the same day -- so "the latest release" is the wrong
# question. The right one is "the latest release FOR THE IGNITION WE RUN".
#
# Read from the compose files rather than written here, so bumping Ignition
# cannot leave this stale.
# Matches the literal tag AND the ${IGNITION_VERSION:-8.3.8} form the compose
# files carry now -- the DEFAULT is the repo's declared version. Without the
# first alternative this reads nothing and the script exits 2, which looks like
# a missing compose file rather than a regex that fell behind a change three
# directories away.
IGN_VERSION="$(sed -n \
  -e 's|.*image: *inductiveautomation/ignition:\${IGNITION_VERSION:-\([0-9.]*\)}.*|\1|p' \
  -e 's|.*image: *inductiveautomation/ignition:\([0-9][0-9.]*\) *$|\1|p' \
                 stacks/*/compose.yaml | head -1)"
IGN_SERIES="${IGN_VERSION%.*}"      # 8.3.8 -> 8.3
[ -n "$IGN_SERIES" ] || { echo "cannot read the Ignition version from stacks/*/compose.yaml" >&2; exit 2; }

# A FLOOR, not just a match. This stack is 8.3+ and will not go back: 8.1 is a
# different Perspective, a different resource format and a different module ABI,
# and an 8.1 module on an 8.3 gateway does not fail loudly -- it is logged as a
# mismatch and skipped, so the component simply never appears. Upstreams that
# publish parallel channels keep cutting 8.1 releases indefinitely, so without a
# floor the newest tag in a repo is regularly the one we must never take.
MIN_SERIES="8.3"

series_lt() {  # series_lt A B -- true when A is an older series than B
  local am="${1%%.*}" an="${1##*.}" bm="${2%%.*}" bn="${2##*.}"
  [ "$am" -lt "$bm" ] && return 0
  [ "$am" -eq "$bm" ] && [ "$an" -lt "$bn" ] && return 0
  return 1
}

if series_lt "$IGN_SERIES" "$MIN_SERIES"; then
  echo "refusing to run: compose says Ignition $IGN_VERSION, and this repo is" >&2
  echo "$MIN_SERIES+ only. Nothing here is verified below that." >&2
  exit 2
fi
INTERVAL="${WATCH_INTERVAL:-1800}"

# repo<TAB>purpose<TAB>tag filter. The filter is a shell glob applied to the tag,
# with %IGN% replaced by the Ignition series we run -- leave it empty where the
# upstream publishes one stream for all versions. Add a line to watch another
# module.
REPOS=$(cat <<'EOF'
ia-tgoetz/ArchitectureBuilderReactFlow	Architecture Builder Perspective component	
ia-tgoetz/ReactFlowPerspectiveModule	Hierarchy chart / DB schema / JSON editor	
mussonindustrial/embr	Embr Charts (Perspective chart components)	releases/%IGN%/*
EOF
)

latest_release() {  # latest_release <repo> [tag-glob]
  # Falls back to the last pushed commit when a repo publishes no releases --
  # ReactFlowPerspectiveModule ships its .modl committed in build/ and has never
  # cut one, so "no releases" must not read as "no news".
  local repo="$1" filter="${2:-}" tag=""
  filter="${filter//%IGN%/$IGN_SERIES}"

  # With a filter, ask for the RELEASE LIST and take the newest tag that
  # matches -- `releases/latest` is whatever was published last, which on a
  # multi-channel repo is regularly the wrong Ignition version.
  if [ -n "$filter" ]; then
    local candidates=""
    if command -v gh >/dev/null 2>&1; then
      candidates=$(gh api "repos/$repo/releases" --jq '.[].tag_name' 2>/dev/null || true)
    else
      candidates=$(curl -sf --max-time 15 "https://api.github.com/repos/$repo/releases" \
                   | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' || true)
    fi
    while read -r c; do
      [ -z "$c" ] && continue
      # Belt and braces: even if a filter were widened by hand, never return a
      # channel older than the floor.
      local cs="${c#releases/}"; cs="${cs%%/*}"
      case "$cs" in
        [0-9]*.[0-9]*) series_lt "$cs" "$MIN_SERIES" && continue ;;
      esac
      case "$c" in $filter) echo "$c"; return 0 ;; esac
    done <<< "$candidates"
    echo "none-for-$IGN_SERIES"
    return 0
  fi
  # `gh api` prints its error body on STDOUT for a 404, so a repo with no
  # releases yielded a pin of {"message":"Not Found"...} that then "changed"
  # on every poll. Anything that is not a plausible tag is treated as absent.
  if command -v gh >/dev/null 2>&1; then
    tag=$(gh api "repos/$repo/releases/latest" --jq '.tag_name' 2>/dev/null || true)
    case "$tag" in *'{'*|*'Not Found'*|'') tag="" ;; esac
    [ -z "$tag" ] && tag="commit:$(gh api "repos/$repo" --jq '.pushed_at' 2>/dev/null || true)"
  else
    tag=$(curl -sf --max-time 15 "https://api.github.com/repos/$repo/releases/latest" \
          | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' || true)
    [ -z "$tag" ] && tag="commit:$(curl -sf --max-time 15 "https://api.github.com/repos/$repo" \
          | sed -n 's/.*"pushed_at": *"\([^"]*\)".*/\1/p' || true)"
  fi
  case "$tag" in *'{'*|'') tag="unknown" ;; esac
  echo "$tag"
}

pinned_for() { grep -E "^$1[[:space:]]" "$PINS" 2>/dev/null | awk '{print $2}' | head -1; }

check_once() {
  local drift=0
  while IFS=$'\t' read -r repo purpose filter; do
    [ -z "$repo" ] && continue
    local now pin
    now=$(latest_release "$repo" "$filter")
    pin=$(pinned_for "$repo")
    if [ -z "$pin" ]; then
      echo "UNPINNED $repo is at $now ($purpose) -- run --pin to record it"
      drift=1
    elif [ "$now" != "$pin" ]; then
      echo "UPDATE $repo $pin -> $now ($purpose)  https://github.com/$repo/releases"
      drift=1
    elif [ "${QUIET:-0}" != "1" ]; then
      # Say "ok" by default. A one-shot check that prints NOTHING when all is
      # well is indistinguishable from one that failed to run -- which is
      # exactly how it read the first time anyone tried it.
      echo "ok $repo $pin"
    fi
  done <<< "$REPOS"
  return $drift
}

case "${1:-}" in
  --pin)
    : > "$PINS"
    {
      echo "# Upstream module versions this stack is verified against."
      echo "# Regenerate with scripts/watch-upstream.sh --pin; commit the diff."
      echo "# Selected FOR IGNITION $IGN_VERSION -- a module built for another"
      echo "# minor will not load, and the gateway only logs a mismatch."
    } >> "$PINS"
    while IFS=$'\t' read -r repo purpose filter; do
      [ -z "$repo" ] && continue
      printf '%s\t%s\n' "$repo" "$(latest_release "$repo" "$filter")" >> "$PINS"
    done <<< "$REPOS"
    echo "pinned:"; grep -v '^#' "$PINS"
    ;;
  --upgrade)
    # Detection without a route to act on it is half a mechanism: the watcher
    # would tell you the Architecture Builder had moved and then leave you to
    # hand-edit a filename, a URL and a sha256 in modules.manifest, which is
    # exactly the sort of three-place edit this repo keeps getting wrong.
    #
    # This does the mechanical part only. It deliberately does NOT install:
    # a module is a signed binary the gateway will execute, and pinning a new
    # version is a decision. The manifest diff is what you review.
    need=0
    while IFS=$'\t' read -r repo purpose filter; do
      [ -z "$repo" ] && continue
      now=$(latest_release "$repo" "$filter")
      pin=$(pinned_for "$repo")
      [ "$now" = "$pin" ] && continue
      case "$now" in commit:*|unknown) 
        echo "skip $repo -- publishes no releases, nothing to fetch"
        continue ;;
      esac

      # The release asset is a bare name with the version only in the tag, so
      # the manifest's filename column is what restores it -- see the note in
      # modules.manifest. Keep that convention.
      line=$(grep -n "releases/download/[^/]*/[^ |]*" "$MANIFEST" | grep "$repo" | head -1 || true)
      if [ -z "$line" ]; then
        echo "skip $repo -- not in $MANIFEST"
        continue
      fi
      lineno=${line%%:*}
      old=$(sed -n "${lineno}p" "$MANIFEST")
      old_file=$(printf '%s' "$old" | awk -F' *\\| *' '{print $1}')
      gateways=$(printf '%s' "$old" | awk -F' *\\| *' '{print $2}')
      desc=$(printf '%s' "$old" | awk -F' *\\| *' '{print $5}')
      old_url=$(printf '%s' "$old" | awk -F' *\\| *' '{print $4}')
      asset=$(basename "$old_url")
      new_url="https://github.com/$repo/releases/download/$now/$asset"
      # base name without the old version suffix, then the new one appended
      base=$(printf '%s' "$old_file" | sed -E 's/-[0-9]+(\.[0-9]+)*\.modl$//; s/\.modl$//')
      new_file="$base-${now#v}.modl"

      tmp=$(mktemp)
      # --max-time 300: same ceiling get-modules.sh uses for the same class of
      # file -- a .modl is tens of MB, so this wants longer than the plain API
      # calls above, not the same 15s.
      if ! curl -sfL --max-time 300 "$new_url" -o "$tmp"; then
        echo "FAIL $repo -- cannot download $new_url"
        rm -f "$tmp"; need=1; continue
      fi
      # Check it is a real module before trusting the hash of it: a 404 page
      # hashes just as happily as a .modl does.
      if ! head -c2 "$tmp" | grep -q 'PK'; then
        echo "FAIL $repo -- $new_url is not a zip/modl"
        rm -f "$tmp"; need=1; continue
      fi
      sum=$(sha256sum "$tmp" | awk '{print $1}')
      rm -f "$tmp"

      printf '%s | %s | %s | %s | %s\n' \
        "$new_file" "$gateways" "$sum" "$new_url" "$desc" > "$MANIFEST.newline"
      # `sed -i` is not portable (BSD wants an argument); rewrite the file.
      awk -v n="$lineno" -v repl="$(cat "$MANIFEST.newline")" \
        'NR==n {print repl; next} {print}' "$MANIFEST" > "$MANIFEST.tmp"
      mv "$MANIFEST.tmp" "$MANIFEST"
      rm -f "$MANIFEST.newline"
      echo "UPGRADED $repo $pin -> $now"
      echo "  file $old_file -> $new_file"
      echo "  sha256 $sum"
      need=1
    done <<< "$REPOS"

    if [ "$need" = "0" ]; then
      echo "nothing to upgrade -- every pin matches upstream"
      exit 0
    fi
    "$0" --pin >/dev/null
    echo
    echo "Now, in order:"
    echo "  git diff scripts/modules.manifest scripts/upstream.pins   # review the pin"
    echo "  make get-modules                                          # fetch + hash-check"
    echo "  scripts/ign-modules.sh ignition                           # install (raises the"
    echo "                                                            #   commissioning gate)"
    echo "  make verify-demos                                         # prove nothing broke"
    ;;

  --watch)
    # One line per NEW release, not one per poll -- the caller turns each line
    # into a notification, so a repeat every 30 minutes would be spam.
    #
    # A plain file rather than an associative array: `declare -A` is bash 4 and
    # macOS ships bash 3.2, which is the break this repo has already been bitten
    # by four times. `case` on a delimited string does the same job everywhere.
    seen=""
    export QUIET=1
    while true; do
      while read -r line; do
        [ -z "$line" ] && continue
        case "$seen" in
          *"|$line|"*) ;;
          *) seen="$seen|$line|"; echo "$line" ;;
        esac
      done < <(check_once || true)
      sleep "$INTERVAL"
    done
    ;;
  *)
    check_once
    ;;
esac
