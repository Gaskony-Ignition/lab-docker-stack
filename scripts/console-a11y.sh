#!/usr/bin/env bash
# Does the console's own HTML (control/error_page.py, stacks/npm/gen-down-page.py)
# meet WCAG 2.1 AA? Not the Demos tab itself -- that is a Perspective page in
# GatewayAdmin, covered by the estate a11y programme -- but the plain-HTML
# status pages wd-control and the proxy serve when a .test name has nothing,
# or not yet enough, behind it. Every viewer of this stack hits one of these
# before they ever reach a gateway.
#
# HOST ONLY, like release.sh: the toolkit's a11y-check.js lives outside this
# repo (the checkout IGNITION_TOOLKIT points at), so it cannot be a step
# inside the portable toolbox -- same reason release.sh itself refuses to run
# in there.
#
# It checks RENDERED SOURCE, not a live wd-control: these pages return 503/502/
# 404 by design (that is the whole point of them), and a11y-check.js treats any
# non-2xx response as a fetch error, not content to scan. The markup is pure
# and deterministic -- title/body strings in, HTML out -- so rendering it here
# from the same functions service.py and gen-down-page.py call is exactly what
# a viewer would see, without needing a demo actually stopped to prove it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${WD_TOOLBOX:-}" = "1" ]; then
  die "console-a11y runs on the HOST, not inside the toolbox.
     The toolkit it checks against (IGNITION_TOOLKIT)
     is not part of this repo and is not mounted in there -- same reason
     release.sh itself refuses to run in the toolbox.

         make console-a11y"
fi

TOOL="${A11Y_CHECK:-${IGNITION_TOOLKIT:?set IGNITION_TOOLKIT to the ignition-claude-toolkit checkout}/plugins/ignition/skills/verify-view/tool/a11y-check.js}"
[ -f "$TOOL" ] || die "a11y-check.js not found at $TOOL (set A11Y_CHECK)"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$REPO_ROOT"

# The proxy's fallback page: one template, no branches worth sampling twice.
python3 stacks/npm/gen-down-page.py > "$WORK/down.html"

# control/error_page.py's one template, in the three shapes its callers pass:
# plain (no link), with the console link, and with the reload timer + manual
# link. Every _send_html call site is one of these three shapes.
PYTHONPATH="$REPO_ROOT/control" python3 - "$WORK" <<'PY'
import sys
import error_page
work = sys.argv[1]
samples = {
    "plain.html": error_page.render(
        "Not a gateway",
        "<p><code>bogus.test</code> is not one of this stack's Ignition "
        "gateways, so there is no login to perform here.</p>"),
    "with-link.html": error_page.render(
        "The EAM Demo is not running",
        "<p><code>edge1.test</code> is the front door of "
        "<code>ignition-edge1</code>, which the <b>EAM Demo</b> starts.</p>"
        "<p>Press <b>Start</b> on the <b>EAM Demo</b> card on the "
        "<a href='https://console.test/data/perspective/client/GatewayAdmin'>"
        "demo console's Demos tab</a>, wait for the card to read "
        "<b>Running</b>, then open this again.</p>"),
    "with-reload.html": error_page.render(
        "ignition-edge3 is still starting",
        "<p>ignition-edge3 accepts connections -- an Ignition gateway "
        "accepts connections about a second in and serves its projects a "
        "minute later.</p><p>This page reloads every ten seconds and will "
        "take you there.</p>", refresh=10),
}
for name, html in samples.items():
    open(f"{work}/{name}", "w").write(html)
PY

urls=()
for f in "$WORK"/*.html; do urls+=("file://$f"); done

say "console pages (WCAG 2.1 AA)"
if ! node "$TOOL" --web "${urls[@]}" --json "$WORK/result.json"; then
  die "console-a11y found findings above -- fix control/error_page.py or
     stacks/npm/gen-down-page.py, or record a narrow, reasoned exception."
fi
ok "console pages: 0 findings"
