#!/usr/bin/env bash
#
# Build the whole demonstration on a machine that has never seen it.
#
#   ./wd bootstrap                 the normal way in
#   ./wd bootstrap --no-restart    stop before the one restart (see step 8)
#   ./wd bootstrap --stack-only    containers and projects, no gateway wiring
#   ./wd bootstrap --keep-up       leave every stack running (needs ~8 GB)
#
# It finishes on the CORE ALONE -- front door, hub gateway, console. Everything
# is built and stays built; a demonstration starts what it needs from the Demos
# page. Bootstrap is a BUILD, and a build should not leave 8 GB running.
#
# It builds in stages -- the hub, then one demo's gateways at a time, started,
# wired and stopped again -- so it fits a machine with ~5 GB to spare (step 5).
#
# Safe to re-run, and expected to be: every step checks before it acts. It will
# not overwrite a .env, reissue a CA, re-approve an approved certificate, or
# reconfigure a gateway that already matches. A second run is how you repair a
# stack someone half-changed, not something to be nervous about.
#
# WHAT THIS HAS TO GET RIGHT, AND WHY IT IS LONG
#
# Only project *resources* live in this repo. Everything else -- the database
# connection, Gateway Network pairing, EAM registration, MQTT module config,
# the historian, the security zone that lets an edge write history, the
# redundant pair -- is gateway state (CLAUDE.md rule 4), and until now it was
# built by hand from the docs. That was fine while the stack lived on one
# machine and nowhere else. It is the whole problem the moment somebody clones
# this: containers start, projects deploy, and every demonstration is dark.
#
# So each of those is a script that already existed and is merely CALLED here,
# in the one order that works. The ordering is the content:
#
#   modules before commissioning   installing a third-party module raises a gate
#                                  that 302-redirects every request to /welcome
#   projects before the restart    Ops carries the AutoScan timer every later
#                                  deploy needs, and cannot scan itself in
#   database before store-forward  the historian is backed by it
#   GAN before EAM                 EAM registration rides the Gateway Network
#   redundancy last of the wiring  the backup pulls the master's finished
#                                  configuration in one sync, so anything set
#                                  after this has to sync again
#
# OPTIONAL STEPS FAIL SOFT. A missing third-party module, an edge that has not
# finished starting, a broker that is still coming up -- none of those should
# take the whole bootstrap down, because the parts that did work are worth
# keeping and the summary at the end says exactly what did not. `set -e` is on,
# so anything not explicitly guarded is genuinely fatal.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NO_RESTART=0
STACK_ONLY=0
KEEP_UP=0
for a in "$@"; do
  case "$a" in
    --no-restart) NO_RESTART=1 ;;
    --stack-only) STACK_ONLY=1 ;;
    --keep-up)    KEEP_UP=1 ;;
  esac
done

need_docker

# --- the styling, before anything else ----------------------------------------
# EVERY project here inherits from `Themes`, whose style classes are generated
# and committed alongside the gateway themes in ignition/themes/. If that tree
# is absent the parent project is empty. Step 8 then deploys nothing at
# all -- and it used to do that almost silently: ign-deploy.sh died, the `|| true`
# after it swallowed the exit code, and bootstrap carried on for eight more
# steps and could still finish by reporting that every step succeeded. A stack
# with four healthy gateways and no projects is the worst outcome this script
# has, because everything that reports health reports it correctly.
#
# This check is kept even though the submodule that motivated it is gone. It
# used to catch an ordinary `git clone` -- which does not populate a submodule,
# and the documented clone lines did not say so -- and it now catches whatever
# else empties the tree: a partial checkout, a bad sparse-checkout, an
# interrupted clone. The failure it guards against is the same either way, and
# it is the expensive one: a stack that reports itself perfectly healthy and
# serves no projects.
if [ ! -f "$REPO_ROOT/ignition/themes/themes.json" ]; then
  die "ignition/themes/ is missing -- this clone is incomplete.

     The gateway themes are committed, so a plain 'git clone' has them -- there
     is no submodule to init and nothing to fetch. If they are absent, something
     removed them: 'git checkout -- ignition/themes'."
fi

# Steps that are allowed to fail are recorded rather than fatal, and reported
# together at the end. A wall of green with one warning buried at line 40 is how
# a broken demonstration reaches a customer.
SKIPPED=""
note_skip() { SKIPPED="$SKIPPED
  - $1"; }
try() {  # try <description> <command...>
  local what="$1"; shift
  if "$@"; then return 0; fi
  warn "$what did not complete"
  note_skip "$what"
  return 0
}

TOTAL=13
step() { echo; say "$1/$TOTAL  $2"; }

# --- 1. environment -----------------------------------------------------------
step 1 "environment files and gateway credentials"
"$REPO_ROOT/scripts/make-env.sh"

# --- 2. certificates ----------------------------------------------------------
# Before the first `up`, and before step 4 seeds them into a volume. The broker
# reads its certificates at boot and crash-loops without them.
step 2 "TLS certificates"
# AN IDEMPOTENCE GUARD MUST NAME EVERYTHING THE STEP PRODUCES, not just the
# first thing. This step makes THREE artefacts and the guard tested two, so a
# machine that had certs/ but no site-certs/ reported "already present",
# skipped the script that would have written it, and died at step 4 -- on
# every re-run, forever. "Run bootstrap again to repair it" is the advice this
# script gives for everything else, and here it could not work.
#
# Re-running is cheap and safe: create-certs.sh reuses an existing CA (it only
# reissues the leaf under it), so browser trust survives a repair run.
if [ -f "$STACKS_DIR/npm/certs/rootCA.pem" ] \
   && [ -f "$STACKS_DIR/npm/site-certs/leaf.pem" ]; then
  ok "certificates already present"
else
  bash "$STACKS_DIR/npm/create-certs.sh"
fi

# --- 3. third-party modules ---------------------------------------------------
# Fetched before anything starts, so the module install in step 6 has them.
# Never fatal: the stack runs without any of them, and get-modules.sh says
# precisely what is missing and what that costs.
step 3 "third-party modules"
"$REPO_ROOT/scripts/get-modules.sh" || note_skip "fetching third-party modules"

# --- 4. shared network and seeded volumes -------------------------------------
# The volumes have to be filled BEFORE anything starts: the broker reads its
# certificates at boot and crash-loops without them, and Postgres runs its init
# scripts only against an empty data directory -- miss that window and the
# databases are never created, silently, on a container that reports healthy.
#
# They are named volumes rather than `./certs`-style bind mounts because a
# relative bind mount is resolved by whatever runs `docker compose`, and from in
# here that is a path on the toolbox's filesystem that the daemon cannot see.
# Docker does not error on a missing bind source; it creates an empty directory.
# See scripts/lib.sh:seed_volume.
step 4 "shared network and seeded volumes"
ensure_backbone
seed_all

# --- 5. the hub first; the rest one demo at a time ---------------------------
# Starting every gateway at once asks ~8 GB, and a 7 GB laptop could not do it
# (30/09/2026). Nothing needs them all up together: every edge and the backup
# only ever talk to the HUB. So the hub is built here, then steps 10-12 start
# one demo's gateways, build them, and stop them again. The peak is the hub,
# Postgres and two gateways -- about 4-5 GB.
#
# The groups are DERIVED from the manifests by role, not typed out: a
# hand-written list once dropped the isolated edges, and every later step
# reported "ignition-edge3 is not running" (11/09/2026).
memory_check 4 "bootstrap (the hub, Postgres, the proxy and the console)"

step 5 "starting the hub"
"$REPO_ROOT/scripts/stack.sh" up npm wd-control ignition postgres
# A run interrupted after step 6 leaves the hub in the commissioning gate, and
# a re-run must clear it rather than die waiting on it.
if ! wait_for_gateway ignition 420; then
  node "$REPO_ROOT/scripts/ign-gw.js" commission --gateway "$(stanza_for ignition)" || true
  wait_for_gateway ignition 180
fi

# Postgres runs its init scripts only on a FIRST start against an empty data
# directory. A volume that came up once without them -- which is exactly what
# the bind-mount bug produced -- will never run them, so the databases have to
# be created idempotently as well. Cheap, and it makes bootstrap able to repair
# a half-built stack rather than only build a clean one.
try "ensuring the Ignition database and role exist" \
  "$REPO_ROOT/scripts/pg-ensure.sh"
# The historian's tables must skip the duplicate rows the edges' rolling buffer
# replays after every hub handover, and take long strings -- on every partition
# Ignition will ever create, which is why it is an event trigger installed now,
# before the first partition exists (scripts/pg-history-guard.sh).
try "making the history tables duplicate-tolerant" \
  "$REPO_ROOT/scripts/pg-history-guard.sh"

# The proxy layer is gateway-independent, so it belongs here rather than after
# the gateway wiring. This was MISSING from bootstrap and found the hard way:
# the work machine ran for weeks with the stack healthy and not one .test name
# routed, because every service still answered on its host port and nothing
# owned creating the proxy hosts. The script derives its table from the stack
# manifests and reconciles -- a changed target is corrected, not skipped.
try "creating the .test proxy hosts in NPM" \
  bash "$REPO_ROOT/stacks/npm/create-proxy-hosts.sh"

# --- 6. modules into the gateways --------------------------------------------
# Two acts, and the second is not optional: registering a module makes the
# gateway demand certificate acceptance on its next start, and until that
# happens it answers {"state":"RUNNING","details":"COMMISSIONING"} and
# 302-redirects EVERY request -- Perspective sessions included -- to /welcome.
# A bootstrap that installed modules and stopped there would leave four
# gateways that look up and serve nothing.
#
# --yes is safe here and nowhere else: these gateways were created minutes ago
# and there is no session to interrupt.
install_modules() {  # install_modules <gateway>
    local gw="$1" missing m
    if ! ls "$REPO_ROOT/modules/$gw"/*.modl >/dev/null 2>&1 \
       && ! ls "$REPO_ROOT/modules/all"/*.modl >/dev/null 2>&1; then
      dim "  $gw: nothing to install"
      return 0
    fi
    # Install and gate-clearing are ONE operation, and only the end state is
    # worth reporting. ign-modules.sh finishes by waiting for the gateway, which
    # is in the commissioning gate at that exact moment BECAUSE the install
    # worked -- so it returns non-zero on success and the summary grew a
    # "did not complete" for a step that had done its job. Run both, then judge
    # by whether the gateway is actually serving again.
    ASSUME_YES=1 "$REPO_ROOT/scripts/ign-modules.sh" --yes "$gw" || true
    node "$REPO_ROOT/scripts/ign-gw.js" commission \
      --gateway "$(stanza_for "$gw")" || true

    if wait_for_gateway "$gw" 180 >/dev/null 2>&1; then
      # "Serving" is not the same as "installed" -- the first from-scratch run
      # died parsing one certificate, registered NOTHING on the hub, and this
      # step still said ok because the gateway (with no new modules to gate on)
      # was serving. Judge by the register instead: every staged .modl must be
      # named in the container's modules.json.
      missing=""
      for m in "$REPO_ROOT/modules/all"/*.modl "$REPO_ROOT/modules/$gw"/*.modl; do
        [ -f "$m" ] || continue
        docker exec "$gw" grep -q "$(basename "$m")" \
          /usr/local/bin/ignition/data/modules.json 2>/dev/null \
          || missing="$missing $(basename "$m")"
      done
      if [ -z "$missing" ]; then
        ok "$gw has its modules and is serving"
      else
        warn "$gw is serving but did NOT register:$missing"
        note_skip "installing$missing on $gw"
      fi
    else
      warn "$gw is still in the commissioning gate"
      warn "finish it by hand at its /welcome page"
      note_skip "clearing the commissioning gate on $gw"
    fi
}
if [ "$STACK_ONLY" -eq 0 ]; then
  step 6 "installing third-party modules on the hub"
  install_modules ignition
else
  step 6 "installing third-party modules -- skipped (--stack-only)"
fi

# --- 6b. trim the modules ------------------------------------------------------
# A stock gateway registers ~32 modules and STARTS THEM ALL. This stack uses
# eight; the rest are drivers for hardware that does not exist, and they cost
# real threads and real idle CPU -- measured on the hub at 343 threads and 16.6%
# of one core doing nothing, of which OPC-UA alone was 29 threads.
#
# HERE, for the same reason the themes are installed here: modules.json is read
# at STARTUP, and step 8 already performs the one restart this bootstrap does.
# Trimming before it means the trim is applied for free and there is no extra
# restart for anyone to forget. It must also come AFTER step 6, so the
# third-party modules exist in the registry and are on the keep-list rather than
# being absent and silently ignored.
#
# Not fatal: an untrimmed gateway is a working gateway, just a heavier one.
step 6b "disabling unused Ignition modules"
try "trimming unused modules" "$REPO_ROOT/scripts/ign-modules-trim.sh" ignition

# --- 7. the themes -----------------------------------------
# THEMES FIRST, and it has to be this way round. A theme is a gateway CONFIG
# resource; one that is present when the gateway STARTS registers with no scan,
# while one added to a running gateway needs the Platform -> Overview "Scan File
# System" -- a UI-only button with no REST route and no in-process API, so
# nothing here could drive it. Installing before the restart that step 8 already
# performs for Ops means the restart registers the themes too, and there is no
# scan step for anyone to forget.
#
# Not fatal on its own: without themes the projects still deploy and every style
# class still resolves. The pages come up with stock IA colours instead of the
# theme's, which is ugly and obvious -- not silent, so it does not need to stop
# the build.
step 7 "installing gateway themes"
try "installing the gateway themes" "$REPO_ROOT/scripts/ign-themes.sh" ignition

# --- 8. the projects ----------------------------------------------------------
# The Ops project carries the AutoScan timer every later deploy relies on to
# apply itself. A project cannot scan itself into existence, so the very first
# install needs the one trigger that does not require a running scan mechanism:
# a gateway restart. This is the only restart the repo performs, it happens only
# while Ops is absent, and from here on `make deploy` never restarts anything.
step 8 "installing projects"
# NOT `|| true`: a silent failure here leaves the gateway with no projects, which
# no later step detects and no health check contradicts -- see the submodule
# precondition at the top for how that used to end.
#
# But NOT `try` either, and this is the subtler half. ign-deploy.sh finishes by
# requesting a scan, and on a FIRST bootstrap that scan cannot possibly work:
# the timer that services it lives in Ops, which this very command has just
# installed and which the gateway has not loaded yet. So the deploy exits
# non-zero having done its job perfectly, and `try` recorded
# "installing the projects did not complete" in the closing summary of every
# clean build. A summary that cries wolf on a healthy run is worse than no
# summary -- it is read once and ignored thereafter.
#
# So remember the status and JUDGE IT BELOW, against the end state that
# actually matters: are the projects on the gateway, and did Ops load. The
# restart that follows is the designed remedy for exactly this case.
deploy_rc=0
"$REPO_ROOT/scripts/ign-deploy.sh" --all ignition || deploy_rc=$?

# grep -c rather than grep -q: grep -q exits at the first match, SIGPIPEs
# `docker logs` while it is still streaming, and under pipefail the whole
# pipeline returns 141 and the test flips to false. It is a race, so it fails
# intermittently -- this restarted an already-loaded hub about every other run.
ops_started="$(docker logs ignition 2>&1 | grep -c 'Starting project: Ops' || true)"

if docker exec ignition test -d /usr/local/bin/ignition/data/projects/Ops/ignition/timer/AutoScan 2>/dev/null \
   && [ "${ops_started:-0}" -gt 0 ]; then
  ok "Ops/AutoScan already loaded -- no restart needed"
elif [ "$NO_RESTART" -eq 1 ]; then
  warn "Ops is not loaded and --no-restart was given."
  warn "Apply it by hand: gateway Config UI -> Projects -> Scan File System"
  note_skip "loading Ops (--no-restart)"
else
  say "restarting the hub once so it picks up the Ops project"
  dim "(the only restart this repo performs -- see scripts/ign-scan.sh)"
  ( cd "$STACKS_DIR/ignition" && docker compose restart >/dev/null )
  wait_for_gateway ignition 420
fi

# The judgement deferred from above. A non-zero deploy is only a real failure if
# the end state is wrong -- and the end state is what every later step depends
# on. Checked AFTER the restart, because the restart is what makes it true.
if ! docker exec ignition test -d /usr/local/bin/ignition/data/projects/Ops/ignition/timer/AutoScan 2>/dev/null; then
  warn "Ops did not install -- every later deploy relies on its AutoScan timer"
  note_skip "installing the projects (Ops/AutoScan is absent)"
elif [ "$deploy_rc" -ne 0 ]; then
  dim "  (the deploy reported $deploy_rc -- expected on a first build, where the"
  dim "   scan it requests has no AutoScan timer to service it yet)"
fi

if [ "$STACK_ONLY" -eq 1 ]; then
  echo
  "$REPO_ROOT/scripts/stack.sh" status
  echo
  say "bootstrap complete (--stack-only: no gateway wiring was done)"
  dim "Run it again without --stack-only to wire the demonstrations up."
  exit 0
fi

# --- 9. the hub's settings ----------------------------------------------------
# Everything a demo later leans on the hub for: the Postgres connection (the
# historian is backed by it), the MQTT Distributor as a TLS broker (the edges
# dial it), the historian, the address it advertises, the sign-in secrets and
# the Designer login. converge.sh is the one list of those settings -- the same
# list `make update` and every demo start re-apply -- so it is called here
# rather than repeated.
step 9 "the hub's settings"
try "the hub's settings (converge's list above names the step)" \
  "$REPO_ROOT/scripts/converge.sh" --starting ignition postgres

# --- 10-12. one demo's gateways at a time ------------------------------------
# Start the group, give it its modules, trimmed module list and themes, wire it
# with converge.sh --starting, stop it. The trim and the themes are read at
# startup, so they take effect on the group's next start -- the first demo --
# exactly as they did when everything was started together.
#
# Redundancy LAST: the backup pulls the master's whole configuration in one
# sync, so it has to come after everything else the hub is given.
build_group() {  # build_group <step> <label> <stack...>
  local n="$1" label="$2" gws="" s; shift 2
  [ "$#" -gt 0 ] || { step "$n" "$label -- none in this repo"; return 0; }
  step "$n" "$label ($*)"
  for s in "$@"; do
    if [ "$(meta_get "$s" KIND)" = gateway ]; then gws="$gws $s"; fi
  done
  memory_check 3 "$label"
  if ! "$REPO_ROOT/scripts/stack.sh" up "$@"; then
    note_skip "starting $*"
    return 0
  fi
  for s in $gws; do
    # Not fatal: a gateway left in the commissioning gate by an interrupted
    # run is cleared by install_modules, which judges the end state itself.
    wait_for_gateway "$s" 420 || true
    install_modules "$s"
  done
  # shellcheck disable=SC2086
  try "trimming unused modules on$gws" "$REPO_ROOT/scripts/ign-modules-trim.sh" $gws
  # shellcheck disable=SC2086
  try "installing the gateway themes on$gws" "$REPO_ROOT/scripts/ign-themes.sh" $gws
  try "the settings for $label (converge's list above names the step)" \
    "$REPO_ROOT/scripts/converge.sh" --starting "$@"
  if [ "$KEEP_UP" -eq 0 ]; then
    try "stopping $label" "$REPO_ROOT/scripts/stack.sh" down "$@"
  fi
}
# shellcheck disable=SC2046
build_group 10 "the EAM and store-and-forward edges" $(gateways_with_role edge)
# shellcheck disable=SC2046
build_group 11 "the Sparkplug edges" $(sparkplug_edges)
backups="$(gateways_with_role backup)"
# shellcheck disable=SC2086
build_group 12 "the redundant backup" $backups ${backups:+ignition-ha}

# --- the migrations are moot on a machine built from nothing -----------------
#
# Every migration in migrations/ exists to carry an EXISTING machine forward to
# an end state a release assumes -- the MQTT network, a generated token, a
# Postgres trigger, a retired container swept up. Bootstrap has just built every
# one of those from scratch, so each would correctly find nothing to do and say
# so, at the cost of a minute of gateway calls proving it.
#
# So they are recorded as applied without being run. The first migration that
# genuinely matters here is the one that arrives with the NEXT release, which is
# what the record exists to make true.
#
# `|| true`: a machine whose first bootstrap predates this file has nothing to
# baseline, and a build must not fail on bookkeeping.
try "recording the migration baseline" "$REPO_ROOT/scripts/migrate.sh" --baseline

# --- 13. hand back a machine running the CORE, not the whole estate -----------
# CLAUDE.md: "This stack is started for a demo and stopped after it -- it must
# never come back on its own." Bootstrap contradicted that on its last line, by
# leaving every gateway it had started running indefinitely on a machine whose
# owner had asked for a build, not a demonstration.
#
# So finish the way the repo says to: the core alone -- front door, hub gateway,
# console -- and let the Demos page start what a demonstration actually needs.
# Everything below is already BUILT; stopping a container does not unbuild it.
if [ "$KEEP_UP" -eq 1 ]; then
  say "leaving every stack running (--keep-up)"
else
  step 13 "back to the core alone"
  dim "  (everything is built and stays built -- start a demo from the Demos page,"
  dim "   or with 'wd demo-start DEMO=<id>'. --keep-up skips this.)"
  # Through the control plane when it is answering, because that is what the
  # Demos page uses and it reconciles rather than guessing. Falling back to a
  # direct stack.sh down of the non-core stacks, since a control plane that is
  # not up must not leave four gateways running.
  if ! "$REPO_ROOT/scripts/wd-demos.sh" stop-all >/dev/null 2>&1; then
    # Everything that is not core, DERIVED: demos.json names the core and the
    # manifests name the rest. This was a typed list, and every stack added
    # after it -- the isolated edges first -- would have been left running by
    # the one path that exists to stop them.
    core="$(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["core"]["stacks"]))' \
             "$REPO_ROOT/demos.json" 2>/dev/null || echo "npm wd-control ignition")"
    non_core=""
    for s in $(stacks_in_order); do
      case " $core " in *" $s "*) ;; *) non_core="$non_core $s" ;; esac
    done
    # shellcheck disable=SC2086
    try "returning to the core" "$REPO_ROOT/scripts/stack.sh" down $non_core
  fi
fi

# --- what happened ------------------------------------------------------------
echo
"$REPO_ROOT/scripts/stack.sh" status
echo

if [ -n "$SKIPPED" ]; then
  warn "bootstrap finished, but these did not complete:"
  printf '%s\n' "$SKIPPED"
  echo

  # A missing Cirrus Link module is not a failure to investigate: it is the
  # normal state of any machine nobody has fetched them onto by hand, and it
  # takes every MQTT step down with it. Without this the summary reads
  # "arming the edge transmitters did not complete" and sends the next person
  # debugging a broker that is working perfectly. get-modules.sh says the same
  # thing at step 3, twelve steps and several minutes of output ago.
  if ! ls "$REPO_ROOT/modules"/*/MQTT-*.modl >/dev/null 2>&1; then
    dim "The Cirrus Link MQTT modules are not on this machine. Every MQTT line"
    dim "above is expected to fail until they are."
    dim ""
    dim "They ARE fetchable -- get-modules.sh sends the Referer the vendor's own"
    dim "\"skip the form\" link sends, and the hash is checked either way. So this"
    dim "means the download itself failed: no internet, or the pinned release has"
    dim "moved. Re-run 'get-modules.sh' on its own to see which."
    dim ""
    dim "What that actually costs: Site 2's Sparkplug road, and nothing else."
    dim "Site 1 reaches the hub over the Gateway Network, so store-and-forward,"
    dim "EAM, the style demonstration and redundancy all work without them."
    echo
  fi

  dim "Each is safe to re-run on its own, or run 'bootstrap' again -- every"
  dim "step checks before it acts."
else
  say "bootstrap complete -- every step succeeded"
fi

echo
dim "Where to look:"
dim "  ./wd status                       health and URL for every service"
dim "  ./wd redundancy-status            the redundant pair"
dim "  Site1 / Site2  ->  /admin         drive the style demonstration"
dim "  GatewayAdmin                      EAM, Store & Forward, Redundancy"
dim "  docs/REDUNDANCY.md docs/STORE-FORWARD.md docs/EAM.md"
