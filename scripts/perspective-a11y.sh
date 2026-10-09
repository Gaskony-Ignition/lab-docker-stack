#!/usr/bin/env bash
# Does GatewayAdmin, Site1, Site2 and the Sparkplug Edge project meet WCAG
# 2.1 AA? Reads a11y.json at the repo root: one entry per project (its
# page-config and which gateway credential name in the toolkit's
# credentials file serves it), plus this repo's own narrow exceptions.
#
# HOST ONLY, like release.sh and console-a11y.sh: the toolkit
# (IGNITION_TOOLKIT) is not part of this repo and is not
# mounted in the toolbox.
#
# It checks the LIVE gateways, unlike console-a11y.sh's rendered-from-source
# pages -- Perspective has no equivalent "render offline" path, and axe on a
# real page is what the toolkit skill is built for. Deploy before running
# this (make deploy PROJECT=<name> / make sparkplug-deploy): it scans what is
# ALREADY on the gateway, same as verify-demos.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${WD_TOOLBOX:-}" = "1" ]; then
  die "perspective-a11y runs on the HOST, not inside the toolbox.
     Same reason as console-a11y.sh and release.sh: the toolkit this checks
     against is not mounted in there.

         make perspective-a11y"
fi

TOOL="${A11Y_CHECK:-${IGNITION_TOOLKIT:?set IGNITION_TOOLKIT to the ignition-claude-toolkit checkout}/plugins/ignition/skills/verify-view/tool/a11y-check.js}"
[ -f "$TOOL" ] || die "a11y-check.js not found at $TOOL (set A11Y_CHECK)"

CFG="$REPO_ROOT/a11y.json"
[ -f "$CFG" ] || die "$CFG not found -- a repo with Perspective projects lists them there"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
python3 -c "
import json, sys
cfg = json.load(open('$CFG'))
json.dump(cfg.get('exceptions', []), open('$WORK/exceptions.json', 'w'))
for p in cfg['projects']:
    print(p['name'] + '\t' + p['pageConfig'] + '\t' + p['gateway'])
" > "$WORK/projects.tsv"

cd "$REPO_ROOT"
fail=0
while IFS=$'\t' read -r name pageconfig gateway; do
  say "perspective-a11y: $name ($gateway)"
  node "$TOOL" "$name" --page-config "$pageconfig" --gateway "$gateway" \
    --exceptions "$WORK/exceptions.json" || fail=1
done < "$WORK/projects.tsv"

if [ "$fail" -ne 0 ]; then
  die "perspective-a11y found findings above -- fix the project's stylesheet
     or view, or record a narrow, reasoned exception in a11y.json."
fi
ok "perspective-a11y: all projects 0 findings"
