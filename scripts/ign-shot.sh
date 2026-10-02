#!/usr/bin/env bash
#
# Screenshot a Perspective page, and look at the result.
#
#   scripts/ign-shot.sh [PROJECT] [PAGE] [OUT]
#   make shot PROJECT=Site1 PAGE=demo
#   make shot PROJECT=GatewayAdmin PAGE= TAB=Redundancy
#
# TAB is for the demo console specifically: it is ONE page carrying five tabs,
# so four of its five screens are reachable only by pressing a button and could
# not be screenshot-verified at all. TAB is the button's LABEL as the customer
# reads it (`TAB="Store & Forward"`), because Perspective's data-component-path
# is a positional index and carries nothing of the component's name. CLICK=
# takes any Playwright selector for anything else.
#
# The shell half exists for one reason: gateway_url. There are two addresses for
# every gateway -- a container name inside the toolbox, a host port outside --
# and picking the wrong one is invisible, because a host-port URL reaches
# nothing from inside the toolbox and every script then reports the gateway as
# down. lib.sh already knows which; nothing here should build a URL by hand.
#
# See scripts/ign-shot.js for what it checks beyond taking the picture.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROJECT="${PROJECT:-${1:-Site1}}"

# `${PAGE+set}`, not `${PAGE:-demo}`: an EXPLICITLY EMPTY page is a real request,
# not an unset one. GatewayAdmin has no `demo` route -- its only route is `/` --
# so `make shot PROJECT=GatewayAdmin PAGE=` must mean the root, and with `:-` it
# silently meant `demo` instead and photographed an empty page. A tool whose
# wrong answer looks like a broken project is the thing this script exists to
# stop, so it should not do it itself.
if [ -n "${PAGE+set}" ]; then PAGE="$PAGE"; else PAGE="${2:-demo}"; fi
GATEWAY="${GATEWAY:-ignition}"
TAB="${TAB:-}"
CLICK="${CLICK:-}"
# A tab shot must not overwrite the root shot: two different screens under one
# filename is how a stale picture gets read as the new one. The label can carry
# spaces and an ampersand, so it is squashed to something a filename can hold.
TAB_SLUG="$(printf '%s' "$TAB" | tr -c 'A-Za-z0-9' '-' | tr -s '-' | sed 's/^-//;s/-$//')"
WAIT="${WAIT:-12000}"
# The viewport, because a page is only "fits without scrolling" AT A SIZE. The
# default is the smaller, meaner window; WIDTH/HEIGHT is how you photograph the
# size a screen actually is -- verifying the Sparkplug tab needed both, and
# without this the only 1920x1080 evidence came from a hand-run node command
# that nobody else would reproduce.
WIDTH="${WIDTH:-1440}"
HEIGHT="${HEIGHT:-900}"

need_docker
cd "$REPO_ROOT"

# URL= overrides the gateway lookup, and the reason is the redundancy demo: the
# pair's front door (https://ignition.test) is not a gateway, it is HAProxy in
# front of both halves, so it has no stanza to look up. Photographing it is the
# only way to verify the thing that demo claims -- that one address keeps
# answering while the half behind it changes. Anything else should still go
# through GATEWAY=, which knows the container-vs-host-port difference.
if [ -n "${URL:-}" ]; then
  # Name the file after what was actually photographed. Leaving GATEWAY's
  # default in the name would file a picture of the front door under the hub,
  # which is the one confusion this whole demo exists to clear up.
  WHO="$(printf '%s' "$URL" | sed 's#^[a-z]*://##; s#[:/].*##')"
else
  require_gateway "$GATEWAY"
  URL="$(gateway_url "$GATEWAY")"
  WHO="$GATEWAY"
  # An Edge gateway runs exactly ONE project and it is always called `Edge`, so
  # the obvious `.../client/Site1/demo` 404s there and looks exactly like a
  # failed EAM push. Catch it here rather than letting someone read a 404 as a
  # broken deploy -- warn rather than refuse, because a deliberate check of the
  # 404 is a legitimate thing to want.
  # edge-isolated too: the Sparkplug demo's edges run one `Edge` project as
  # well -- deployed directly rather than pushed by EAM, same rule.
  case "$(meta_get "$GATEWAY" ROLE "")" in
    edge|edge-isolated)
      if [ "$PROJECT" != Edge ]; then
        warn "$GATEWAY is an Edge gateway: it serves one project, always called 'Edge'"
        dim  "  PROJECT=$PROJECT will 404 there. Try: make shot GATEWAY=$GATEWAY PROJECT=Edge"
      fi
      ;;
  esac
fi

OUT="${OUT:-${3:-.shots/${WHO}-${PROJECT}-${PAGE:-root}${TAB_SLUG:+-$TAB_SLUG}.png}}"
mkdir -p "$(dirname "$OUT")"

say "$WHO -> $PROJECT/$PAGE${TAB:+ (tab: $TAB)}"
node "$REPO_ROOT/scripts/ign-shot.js" \
  --url "$URL" \
  --project "$PROJECT" --page "$PAGE" --out "$OUT" --wait "$WAIT" \
  --width "$WIDTH" --height "$HEIGHT" \
  ${TAB:+--click-text "$TAB"} ${CLICK:+--click "$CLICK"}
