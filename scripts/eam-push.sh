#!/usr/bin/env bash
#
# Push the site projects to the edges over EAM, and prove they arrived.
#
#   scripts/eam-push.sh                 both edges
#   scripts/eam-push.sh ignition-edge1  just the one
#
# WHY THIS IS PART OF THE BUILD AND NOT ONLY A DEMO BUTTON
#
# The /admin page can push at any time, and that is the demonstration. But until
# the first push an edge is running the stub `Edge` project it creates for
# itself at commissioning: no dashboard, no styles, and -- the part that bites
# silently -- no gw_trial, because that is an inherited resource of
# Toolbox_Styles and only arrives with the push. An edge left in that state
# looks completely healthy, answers StatusPing RUNNING, and goes trial-expired
# about two hours later (as every unlicensed gateway does). Verified on a clean Windows build 05/08/2026: both
# edges expired, both holding nothing but `Edge`, nothing in any log to say why.
#
# So the build does the first push. Everything after that is the demo.
#
# VERIFY ON THE TARGET, NEVER ON THE TASK RESULT
#
# EAM's Send Project is differential and compares resource METADATA rather than
# content, so "Success" means "nothing to do" exactly as readily as it means
# "sent". The only honest check is to read a resource on the edge that could
# only have come from the hub -- here the demo_styles library, which the stub
# Edge project does not have.
#
# EXISTENCE IS NOT PROOF, because the file may be left over from an EARLIER
# push. That is not hypothetical: this check pointed at the retired
# toolbox_styles library after the Styles_Template cutover, and reported a
# confident success for edge2 by reading the value from the PREVIOUS push
# seconds before EAM deleted the file. Compare the VALUE against the hub's.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Which project goes to which agent, and the agent's gateway system name.
# Both maps come from the edge's own manifest. EAM_AGENT is checked by
# validate against the -n in the edge's compose file, so it is the same on
# every machine; the pairs must also match gateway_admin.AGENTS in the
# GatewayAdmin project, because that page runs the tasks BY NAME -- validate's
# topology check ties those together too.
edge_project() { meta_get "$1" SITE_PROJECT; }
edge_agent()   { meta_get "$1" EAM_AGENT; }

# A resource that exists only once the flattened push has landed. The edge's own
# stub project has no script library at all, so this cannot false-positive.
PROOF='/usr/local/bin/ignition/data/projects/Edge/ignition/script-python/demo_styles/code.py'
# The same resource on the hub, per project -- what the edge's copy must match.
hub_source() { printf '/usr/local/bin/ignition/data/projects/%s/ignition/script-python/demo_styles/code.py' "$1"; }
# `|| true` is load-bearing, not defensive habit. lib.sh sets `set -euo pipefail`,
# so on the FIRST EVER push -- when the proof file legitimately does not exist yet
# -- grep exits 1, pipefail propagates it, and the bare assignment
# `got="$(chosen_pack_in ...)"` below killed the whole script on its first check.
# The symptom was a log that stopped dead after "waiting for X to land", with no
# "never arrived" warning and, worse, NO PUSH TO THE SECOND EDGE AT ALL.
# Invisible on any machine that already had a pushed file, because then grep
# succeeds -- so it only ever broke a build with nothing left over from before.
chosen_pack_in() {
  docker exec "$1" sh -c "grep '^CHOSEN_PACK' '$2' 2>/dev/null" 2>/dev/null \
    | head -1 | sed 's/.*= *//; s/"//g' || true
}

need_docker
require_gateway ignition

EDGES=("$@")
[ ${#EDGES[@]} -gt 0 ] || EDGES=( $(gateways_with_role edge) )

failed=0
for edge in "${EDGES[@]}"; do
  project="$(edge_project "$edge")"
  agent="$(edge_agent "$edge")"
  [ -n "$project" ] || die "no project mapping for '$edge'"

  if ! gateway_running "$edge"; then
    warn "$edge is not running -- skipping"
    failed=1
    continue
  fi

  say "pushing $project to $agent"
  if ! node "$REPO_ROOT/scripts/ign-gw.js" eam-push \
       --gateway local --project "$project" --agent "$agent"; then
    warn "the push task for $agent did not run"
    failed=1
    continue
  fi

  # The task is asynchronous -- the force call returns as soon as it is
  # scheduled, so the resources are still in flight here.
  say "waiting for $project to land on $edge"
  want="$(chosen_pack_in ignition "$(hub_source "$project")")"
  [ -n "$want" ] || die "no CHOSEN_PACK in $project on the hub -- nothing to verify against"
  landed=0
  # 8 minutes, not the 60s this used to allow. EAM flattens the parent into the
  # child, and the v2 template is 81 packs -- 11,191 files -- so the push is now
  # minutes of small-file I/O, and slowest on Windows where Docker Desktop makes
  # that expensive. Measured mid-push on a Windows host: ~2,570 files per 30s.
  # The old deadline expired while the push was still visibly landing, so the
  # script called a working push a failure. Waiting longer costs nothing when it
  # succeeds -- the loop breaks the moment the value matches.
  for _ in $(seq 1 240); do
    # The VALUE has to match the hub's, not merely be present: a file left by an
    # earlier push satisfies `test -f` while proving nothing about this one.
    got="$(chosen_pack_in "$edge" "$PROOF")"
    if [ -n "$got" ] && [ "$got" = "$want" ]; then
      landed=1
      break
    fi
    sleep 2
  done

  if [ "$landed" -eq 1 ]; then
    ok "$edge has $project  (CHOSEN_PACK = $want, matches the hub)"
  else
    [ -n "${got:-}" ] && warn "$edge still reads CHOSEN_PACK = $got, hub has $want"

    warn "$project never arrived on $edge."
    warn "Check the task history: GET /data/eam/api/v1/eam-tasks/history"
    failed=1
  fi
done

exit "$failed"
