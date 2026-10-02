#!/usr/bin/env bash
#
# Put the third-party .modl files where ign-modules.sh expects them.
#
#   scripts/get-modules.sh            fetch what can be fetched, report the rest
#   scripts/get-modules.sh --check    report only, download nothing
#
# WHY THIS EXISTS
#
# `modules/` is gitignored: these are vendor binaries, some of them licensed,
# and putting ~140 MB of them in a repo the team clones would both bloat every
# checkout forever and redistribute software that is not ours to redistribute.
# But a teammate who clones this repo and runs bootstrap needs them, so
# "download them yourself somehow" is not good enough either. This is the
# middle: fetch what is publishable, verify everything by hash, and be
# completely explicit about what is missing and why.
#
# THREE WAYS A MODULE CAN ARRIVE, in the order this tries them:
#
#   1. Already present and correct.       Nothing happens. Re-runs are free.
#   2. A URL in scripts/modules.manifest. Downloaded and hash-checked.
#   3. $MODULES_MIRROR/<filename>.        A base URL your team controls -- an
#      S3 bucket, an internal web server, a shared gateway. This is how the
#      Cirrus Link modules travel between machines without entering git.
#      Still hash-checked, so a mirror cannot drift from what was tested.
#
# Anything left is reported with the page to get it from, and this script exits
# 0 anyway. That is deliberate: the stack comes up and the styling, EAM,
# store-and-forward-over-Gateway-Network and redundancy demonstrations all work
# without any third-party module at all. Failing the whole bootstrap over an
# optional binary would be the wrong trade.
#
# EVERY file is hash-checked, downloaded or not. A module is a signed binary the
# gateway will execute; "the download returned 200" is not the same as "this is
# the file the stack was tested against", and a truncated download is a module
# that installs, fails to load, and logs something about a corrupt archive.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MANIFEST="$REPO_ROOT/scripts/modules.manifest"
MODULES_DIR="$REPO_ROOT/modules"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

[ -f "$MANIFEST" ] || die "no manifest at $MANIFEST"

command -v curl >/dev/null 2>&1 || die "curl is not installed"

# sha256sum (GNU) or shasum -a 256 (macOS). The toolbox image has the first;
# this keeps the script usable on a bare Mac too.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# releases.inductiveautomation.com serves the third-party modules straight out of
# object storage and answers **403 AccessDenied** to a request carrying no
# `Referer` -- plain hotlink protection, not authentication. Any
# inductiveautomation.com referer is accepted (verified 07/08/2026: the bare
# domain, /downloads/third-party-modules and .../8.3.8 all return 200; a foreign
# one returns 403). Curl and wget send no referer, which is why this host looked
# for months like it required a login and the files looked un-fetchable.
#
# This is the vendor's own supported path, not a bypass: the downloads page
# carries a "Skip form and download directly" link whose href is exactly the URL
# in the manifest. Middle-clicking a module there does the same thing -- the
# browser sends the page as the referer, so the download simply works.
referer_for() {  # referer_for <url>  -- '' when the host does not need one
  case "$1" in
    https://releases.inductiveautomation.com/*)
      printf '%s' 'https://inductiveautomation.com/downloads/third-party-modules' ;;
    *) printf '' ;;
  esac
}

fetch() {  # fetch <url> <dest>  -- to a temp file, moved only once it is whole
  local url="$1" dest="$2" tmp referer
  tmp="$(mktemp "${dest}.XXXXXX")"
  referer="$(referer_for "$url")"
  set -- -fsSL --retry 2 --max-time 300 -o "$tmp"
  [ -n "$referer" ] && set -- "$@" -H "Referer: $referer"
  if curl "$@" "$url"; then
    mv "$tmp" "$dest"
    return 0
  fi
  rm -f "$tmp"
  return 1
}

have=0; got=0; missing=0
MISSING_NOTES=""

while IFS='|' read -r filename gateways sha source note; do
  # Trim: the manifest is aligned for reading, so every field has spaces.
  filename="$(printf '%s' "$filename" | tr -d ' ')"
  case "$filename" in ''|'#'*) continue ;; esac
  gateways="$(printf '%s' "$gateways" | tr -d ' ')"
  sha="$(printf '%s' "$sha" | tr -d ' ')"
  source="$(printf '%s' "$source" | tr -d ' ')"
  note="$(printf '%s' "$note" | sed 's/^ *//; s/ *$//')"

  # The canonical copy lives once, under modules/.cache, and is hard-linked (or
  # copied) into each gateway folder. MQTT Transmission is needed by both edges
  # and is 43 MB; downloading it twice, or keeping two copies of a file that is
  # byte-identical, is waste that shows up as a slow bootstrap.
  cache="$MODULES_DIR/.cache/$filename"
  mkdir -p "$MODULES_DIR/.cache"

  if [ -f "$cache" ] && [ "$(sha256_of "$cache")" = "$sha" ]; then
    :
  else
    # Not cached. Maybe a previous hand-drop put it in one of the gateway
    # folders -- adopt that rather than asking for it again.
    adopted=""
    for gw in $(printf '%s' "$gateways" | tr ',' ' '); do
      candidate="$MODULES_DIR/$gw/$filename"
      if [ -f "$candidate" ] && [ "$(sha256_of "$candidate")" = "$sha" ]; then
        cp "$candidate" "$cache"; adopted=1; break
      fi
    done

    if [ -z "$adopted" ]; then
      url=""
      case "$source" in
        https://*) url="$source" ;;
        MANUAL)    [ -n "${MODULES_MIRROR:-}" ] && url="${MODULES_MIRROR%/}/$filename" ;;
      esac

      if [ -n "$url" ] && [ "$CHECK_ONLY" -eq 0 ]; then
        say "fetching $filename"
        if fetch "$url" "$cache"; then
          actual="$(sha256_of "$cache")"
          if [ "$actual" != "$sha" ]; then
            rm -f "$cache"
            die "$filename downloaded but its hash does not match the manifest.
     expected $sha
     got      $actual
     Refusing to install it. If the vendor has republished the file, verify the
     new one yourself and update scripts/modules.manifest in a commit that says
     why."
          fi
          got=$((got + 1))
        else
          warn "could not download $filename from $url"
        fi
      fi
    fi
  fi

  if [ ! -f "$cache" ]; then
    missing=$((missing + 1))
    MISSING_NOTES="$MISSING_NOTES
  $filename
      $note
      put it in modules/$(printf '%s' "$gateways" | cut -d, -f1)/ (or set MODULES_MIRROR)"
    continue
  fi

  # Place it for every gateway that wants it.
  for gw in $(printf '%s' "$gateways" | tr ',' ' '); do
    mkdir -p "$MODULES_DIR/$gw"
    target="$MODULES_DIR/$gw/$filename"
    if [ -f "$target" ] && [ "$(sha256_of "$target")" = "$sha" ]; then
      have=$((have + 1)); continue
    fi
    cp "$cache" "$target"
    have=$((have + 1))
  done
done < "$MANIFEST"

echo
ok "$have module file(s) in place${got:+, $got newly downloaded}"

if [ "$missing" -gt 0 ]; then
  echo
  warn "$missing module(s) could not be fetched automatically:"
  printf '%s\n' "$MISSING_NOTES"
  echo
  dim "  Every module in the manifest has a public URL, so this normally means the"
  dim "  machine could not reach the internet rather than that a file is gated."
  dim "  Check the warning above for the URL that failed."
  echo
  dim "  The stack still comes up without any of them. What you lose:"
  dim "    MQTT Engine / Transmission  Site 2's Sparkplug road on the Store &"
  dim "                                Forward page. Site 1 goes over the Gateway"
  dim "                                Network and is unaffected."
  dim "    Architecture Builder        the Architecture tab in GatewayAdmin."
  dim "    Embr Charts                 nothing -- the dashboard uses stock charts."
  dim "  Styling, EAM distribution and redundancy use no third-party module at all."
  echo
  dim "  To share them with your team without putting binaries in git, host the"
  dim "  files somewhere they can reach and set MODULES_MIRROR to that base URL."
fi
