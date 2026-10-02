#!/usr/bin/env bash
#
# Has anybody changed a gateway by hand? -- the READ-ONLY half of the setup
# scripts.
#
#   scripts/drift.sh            LOOK: run every check, report, record a snapshot
#   scripts/drift.sh --quick    LOOK, hub pair only -- the cheap sweep
#   scripts/drift.sh --json     LOOK, machine-readable on stdout
#   scripts/drift.sh --show     print what was RECORDED -- no gateway calls
#
# WHY THIS EXISTS. Trying things on a live gateway and then resetting is purpose
# 2 of this rig, and every setup script here is idempotent in the sense that
# matters for a bootstrap and the wrong sense for an engineer: re-run it and it
# puts back what it wants, without warning. Nothing said which settings those
# were. This says, before anything is re-run.
#
# IT WRITES NOTHING, ANYWHERE. Not to a gateway, not to a project. Every check
# it calls is a `--check`/`--status`/`check` mode of the script that owns that
# setting -- the comparison is the apply path's own, so this cannot drift from
# what would actually be written. The only file it touches is its snapshot.
#
# THE LOOK/SHOW SPLIT IS scripts/update-check.sh's, for the same reason, and the
# reasoning there is the fuller version of this. Work is split by WHAT IT COSTS:
#
#   LOOK (the default)    log into every gateway, compare, write the snapshot.
#                         MINUTES -- see the cost note below.
#   SHOW (--show)         read the snapshot and print. No gateway, no network,
#                         no credential; its whole cost is sourcing lib.sh,
#                         which every script here pays anyway. Safe in the
#                         toolbox and safe embedded in another command's output.
#
# --show EXITS 0 WHATEVER IT FINDS, deliberately, and so must anything that
# embeds it: "some settings have drifted" is an ordinary answer, and a non-zero
# exit for the ordinary answer is the bug this repo keeps writing (CLAUDE.md).
# LOOK exits 1 when it found drift, because a caller that asked for the sweep
# did ask the question.
#
# THE COST, AND WHY IT SHAPES EVERYTHING HERE
#
# Every `node scripts/ign-gw.js` call launches Chromium and logs the gateway in
# from scratch. Nothing batches, and nothing caches. Measured on this rig
# 21/09/2026: ~17s of gateway call, ~22s wall per check once the rest is counted.
# So the cost of this command is simply how many settings it reads:
#
#   --quick   7 calls    the hub pair -- measured 2m25s
#   full     13 calls    every gateway that is running -- measured 4m55s
#
# (Both before 23/09/2026, when the MQTT settings joined: +3 calls on the hub,
# +1 on Site 2 and +4 on each MQTT demo edge.)
#
# The header printed at the start quotes the number of calls THIS run will make,
# worked out from the plan before any of them happen, so a machine with more
# gateways up says so rather than surprising you four minutes in.
#
# Hence: progress is printed as it goes, every answer is read exactly once and
# held (the four reads in distributor-setup's --status are the same four its
# apply path makes), and --show costs nothing at all.
#
# A GATEWAY THAT IS DOWN IS A NORMAL OUTCOME. Most of these stacks are demos and
# are normally stopped. A down gateway is skipped, recorded as skipped, and never
# fails anything -- but it is NOT reported as "no drift", because nothing was
# read.
#
# WHAT IS NOT COMPARED is in the snapshot's `notCompared`, and it is the most
# important field in the file. Some settings are rewritten on every run BY
# DESIGN and are therefore not drift; some scripts have no want/have at all. A
# check that overstated its coverage would be worse than no check, because the
# clean report would be believed.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

STATE="$(wd_local_dir)/drift.json"
# Why .wd-local and nowhere else: it is the ONE gitignored directory this repo
# makes writable, and stacks/wd-control/compose.yaml mounts it rw while mounting
# the rest of the repo read-only (the "one writable corner" note in lib.sh). So
# the demo console's control plane can read this snapshot without anything else
# in the repo becoming writable, and nothing else should be added to that list.

# A check that quietly stopped running looks exactly like a rig nobody has
# touched. Past this, --show says how old the answer is out loud -- the same
# reasoning as the update check's age and the Demos page's readiness snapshot.
STALE_HOURS=24

MODE=look
QUICK=0
JSON=0
for a in "$@"; do
  case "$a" in
    --show)  MODE=show ;;
    --quick) QUICK=1 ;;
    --json)  JSON=1 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//; /^set -euo/d'
      exit 0 ;;
    *) die "usage: drift.sh [--quick|--json|--show]" ;;
  esac
done

# ---------------------------------------------------------------- the record --
#
# One JSON document, because the console reads it from a Perspective binding and
# a shell reads it with python3 either way. `drift[].settings` entries are SHORT
# PLAIN WORDS on purpose: they are rendered in a tooltip, so a stack trace or a
# JSON fragment in there is a tooltip nobody can read.

read_state() {  # read_state -- the snapshot on stdout, or nothing
  if [ -f "$STATE" ]; then cat "$STATE"; fi
}

show() {
  local raw
  raw="$(read_state)"
  # Never looked -- say nothing at all. An unconfigured machine must not nag.
  if [ -z "$raw" ]; then return 0; fi
  printf '%s' "$raw" | STALE_HOURS="$STALE_HOURS" NOW="$(date +%s)" \
    C_BOLD="$C_BOLD" C_DIM="$C_DIM" C_RESET="$C_RESET" \
    C_YEL="$C_YEL" C_GRN="$C_GRN" python3 -c '
import json, os, sys
E = os.environ
try:
    s = json.load(sys.stdin)
except Exception:
    print("  drift snapshot is unreadable -- re-run: make drift")
    raise SystemExit(0)
secs = max(0, int(E["NOW"]) - int(s.get("at", 0)))
age = ("%dm ago" % (secs // 60)) if secs < 7200 else ("%dh ago" % (secs // 3600))
d, nc = s.get("drift") or [], s.get("notCompared") or []
n = sum(len(x.get("settings") or []) for x in d)
if n:
    print("\n%s%s  %d setting%s on %d gateway%s differ%s from what the setup scripts would write%s  %s(checked %s)%s"
          % (E["C_YEL"], E["C_BOLD"], n, "" if n == 1 else "s", len(d),
             "" if len(d) == 1 else "s", "s" if n == 1 else "", E["C_RESET"],
             E["C_DIM"], age, E["C_RESET"]))
    for x in d:
        print("      %s -- %s" % (x.get("gateway", "?"), x.get("what", "?")))
        for line in (x.get("settings") or []):
            print("        %s" % line)
    print("      %sre-apply what you want back%s, or leave it -- this only reports\n"
          % (E["C_BOLD"], E["C_RESET"]))
else:
    print("%s  ok%s no drift in %d check%s (checked %s)"
          % (E["C_GRN"], E["C_RESET"], len(s.get("checked") or []),
             "" if len(s.get("checked") or []) == 1 else "s", age))
for g in s.get("skipped") or []:
    print("%s  %s was down -- not checked%s" % (E["C_DIM"], g, E["C_RESET"]))
for e in s.get("errors") or []:
    print("%s  could not check %s%s" % (E["C_YEL"], e, E["C_RESET"]))
print("%s  not compared: %d thing(s) -- see .wd-local/drift.json%s"
      % (E["C_DIM"], len(nc), E["C_RESET"]))
if secs >= int(E["STALE_HOURS"]) * 3600:
    print("%s  this answer is %s -- re-run: make drift%s" % (E["C_DIM"], age, E["C_RESET"]))
' || true
  return 0
}

if [ "$MODE" = show ]; then show; exit 0; fi

# ---------------------------------------------------------------- looking -----

need_docker
cd "$REPO_ROOT"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# --json PUTS ONLY THE DOCUMENT ON STDOUT. Everything below reports progress
# with lib.sh's say/ok/dim, which print to stdout, so in --json mode stdout is
# pointed at stderr for the duration and the document is written to fd 3 at the
# end. Without this the caller has to strip a progress log out of the thing it
# is trying to parse -- and the progress cannot simply be dropped instead,
# because a command that costs two and a half minutes and prints nothing while
# it runs is indistinguishable from a hang.
exec 3>&1
if [ "$JSON" -eq 1 ]; then exec 1>&2; fi

# WHAT WOULD BE READ, one record per line, before anything is read.
#
#   check|<gateway>|<what>|<gateway-calls>|<command>
#   skip|<gateway>|<why>
#
# Built from the stack manifests and from what is actually running, so the plan
# IS the cost estimate -- that is what lets the header below quote a number
# rather than an adjective. Down gateways become `skip` records here, which is
# why a stopped demo never reaches a check script at all.
plan() {
  local g
  for g in $(gateways_with_role hub) $(gateways_with_role backup); do
    if ! gateway_running "$g"; then
      printf 'skip|%s|its container is not running\n' "$g"; continue
    fi
    printf 'check|%s|MQTT Distributor: broker certificate, TLS-only, users|4|scripts/distributor-setup.sh --status %s\n' "$g" "$g"
    printf 'check|%s|public address|1|scripts/ign-public-address.sh --check %s\n' "$g" "$g"
    # The backup refuses every config write -- redundancy syncs this one from
    # the master -- so there is nothing there for an engineer to have changed,
    # and ign-designer-idp.sh skips it in its own sweep for the same reason.
    if [ "$(meta_get "$g" ROLE)" != backup ]; then
      printf 'check|%s|Designer login strategy|1|scripts/ign-designer-idp.sh --check %s\n' \
        "$g" "$(stanza_for "$g")"
    fi
    # MQTT Engine: on the master only, for the same reason -- the backup's Engine
    # config arrives by sync. The broker itself is the Distributor check above.
    if [ "$(meta_get "$g" MQTT_MODULE)" = engine ]; then
      printf 'check|%s|MQTT Engine: server, server set, namespace bindings|1|scripts/ign-mqtt.sh check %s\n' "$g" "$g"
      printf 'check|%s|MQTT Engine: alarm events, command blocks, display paths, custom namespace|2|scripts/sparkplug-setup.sh --check %s\n' "$g" "$g"
    fi
  done
  # Redundancy is a property of the PAIR, not of a half, so it is one check that
  # reads whichever halves are up. Its own `check` verb does the skipping.
  if gateway_running ignition; then
    printf 'check|ignition + ignition-backup|redundancy settings|1|scripts/ign-redundancy.sh check\n'
  fi

  if [ "$QUICK" -eq 1 ]; then return 0; fi

  # Both edge roles. An edge-isolated gateway (the Sparkplug demo's) carries the
  # same single Panel session as an ordinary spoke, so it needs the
  # visualization check exactly as much -- see ign-edge-visual.sh on why that is
  # the one place both roles are named.
  for g in $(gateways_with_role edge) $(gateways_with_role edge-isolated); do
    if ! gateway_running "$g"; then
      printf 'skip|%s|its container is not running\n' "$g"; continue
    fi
    printf 'check|%s|Edge Panel visualization|1|scripts/ign-edge-visual.sh --check %s\n' "$g" "$g"
    printf 'check|%s|public address|1|scripts/ign-public-address.sh --check %s\n' "$g" "$g"
    printf 'check|%s|Designer login strategy|1|scripts/ign-designer-idp.sh --check %s\n' \
      "$g" "$(stanza_for "$g")"
    # The Gateway Network edge's transmitter is kept disabled and off the
    # broker (sf-arm.sh), so ign-mqtt.sh leaves it alone and so does this.
    if [ "$(meta_get "$g" MQTT_MODULE)" = transmission ] && [ "$(meta_get "$g" SF_TRANSPORT)" != gan ]; then
      printf 'check|%s|MQTT Transmission: servers, server set|1|scripts/ign-mqtt.sh check %s\n' "$g" "$g"
    fi
    if [ "$(meta_get "$g" SF_TRANSPORT)" = mqtt ] || [ "$(meta_get "$g" ROLE)" = edge-isolated ]; then
      printf 'check|%s|store-and-forward history store|1|scripts/sf-arm.sh --check %s\n' "$g" "$g"
    fi
    if [ "$(meta_get "$g" ROLE)" = edge-isolated ]; then
      printf 'check|%s|MQTT demo transmitter, server-set RPC client|2|scripts/sparkplug-setup.sh --check %s\n' "$g" "$g"
    fi
  done
  return 0
}

# One record of the snapshot, built by python3 so that a gateway name or a drift
# line containing a quote cannot corrupt the file. Cheap: milliseconds against
# the ~17s the gateway call in the same iteration just cost.
record() {  # record <n> <status> <gateway> <what> <lines-file|->
  python3 - "$2" "$3" "$4" "$5" > "$WORK/rec.$1.json" <<'PY'
import json, sys
status, gateway, what, path = sys.argv[1:5]
lines = []
if path != "-":
    with open(path) as fh:
        lines = [l.strip() for l in fh if l.strip()]
json.dump({"status": status, "gateway": gateway, "what": what, "settings": lines},
          sys.stdout)
PY
}

plan > "$WORK/plan"
calls=0
n_checks=0
while IFS='|' read -r kind gw what want cmd; do
  case "$kind" in
    check) n_checks=$((n_checks + 1)); calls=$((calls + want)) ;;
  esac
done < "$WORK/plan"

SCOPE=full
if [ "$QUICK" -eq 1 ]; then SCOPE=quick; fi

if [ "$QUICK" -eq 1 ]; then
  say "drift check -- the hub pair only"
else
  say "drift check -- every gateway that is running"
fi
dim "  $n_checks checks, $calls gateway logins at ~17s each -- about $(( (calls * 17 + 59) / 60 )) minute(s). Nothing is written."
echo

i=0
ci=0
drifted=0
while IFS='|' read -r kind gw what want cmd; do
  case "$kind" in
    skip)
      i=$((i + 1))
      dim "  $gw -- $what, not checked"
      record "$i" skipped "$gw" "$what" -
      continue ;;
  esac

  i=$((i + 1))
  ci=$((ci + 1))
  say "[$ci/$n_checks] $gw -- $what"
  out="$WORK/out.$i"
  rc=0
  # `|| rc=$?` and not a bare call: a check that reports drift EXITS 1, which is
  # the answer, not a failure -- under `set -e` letting it propagate would end
  # the sweep on the first gateway anyone had touched.
  bash $cmd > "$out" 2>&1 || rc=$?
  sed 's/^/    /' "$out"

  sed -n 's/^ *drift: //p' "$out" > "$WORK/lines.$i"
  if [ -s "$WORK/lines.$i" ]; then
    record "$i" drift "$gw" "$what" "$WORK/lines.$i"
    drifted=1
  elif [ "$rc" -eq 0 ]; then
    record "$i" clean "$gw" "$what" -
  else
    # Non-zero with nothing to show for it is NOT drift. Treating it as drift
    # would be the check inventing a finding out of its own failure, and a
    # gateway mid-restart would report settings that were never read.
    printf '%s\n' "the check itself failed (exit $rc): $(tail -3 "$out" | tr '\n' ' ' | cut -c1-200)" \
      > "$WORK/lines.$i"
    record "$i" error "$gw" "$what" "$WORK/lines.$i"
  fi
  echo
done < "$WORK/plan"

# ---------------------------------------------------------------- the snapshot --
#
# notCompared IS NOT A TODO LIST. Most of it is settings that are rewritten on
# every run BY DESIGN, where a re-write is the mechanism and not a mistake, so
# there is no want/have to compare and inventing one would report drift on a
# healthy gateway. It is in the file, and named in --show, so that a clean report
# is never read as "the whole rig is as the scripts left it".
# ONE ENTRY PER PARAGRAPH: a new entry starts in column 1 and its continuation
# lines are INDENTED, which is what the split below keys on. Reflowing a
# paragraph is free; un-indenting a continuation line silently makes it a
# fourth entry.
cat > "$WORK/not-compared" <<'EOF'
sf-arm.sh, the transmitter re-save -- rewritten every run BY DESIGN: that
  re-save IS the step that recomputes the flush type, and skipping it when the
  value looks right is exactly how a gateway ends up armed-looking with
  historyFlushType=NONE underneath. Its history store IS compared.
sf-gan-history.sh -- 3 records rewritten every run by design (a hub security
  zone, a remote tag provider, and edge-sync-settings toggled off then on
  because the historian only restarts when a value changes): not compared. It
  has no want/have anywhere, so there is nothing to compare rather than
  something skipped.
sparkplug-setup.sh -- its --check compares the transmitter, the server sets'
  rpcClientEnabled, Engine's general settings and the AlarmDemoNotify custom
  namespace. Not compared: the hub's alarm journal, the hub's reference tags,
  the action token, the two projects and the edges' tags, which the run
  provisions or deploys rather than compares.
ign-mqtt.sh -- its check compares every server (URL, set, user, CA, keepalive,
  the RPC client's CA and user), the server set and the namespace bindings, and
  reports servers setup would delete. Not compared: the broker password, which
  is encrypted on the gateway.
other setup scripts (ign-secrets.sh, ign-gan.sh, ign-themes.sh,
  sf-historian.sh, pg-ensure.sh, ign-modules-trim.sh, eam-push.sh,
  mqtt-udt-rollout.sh) -- outside this check entirely. A clean report here says
  nothing about any of them.
EOF

AT="$(date +%s)" VERSION="$(stack_version)" MODE_NAME="$SCOPE" \
  python3 - "$STATE.tmp" "$WORK/not-compared" "$WORK" <<'PY'
import glob, json, os, re, sys
out, nc_path, work = sys.argv[1:4]
recs = []
for path in sorted(glob.glob(os.path.join(work, "rec.*.json")),
                   key=lambda p: int(re.search(r"rec\.(\d+)\.json$", p).group(1))):
    with open(path) as fh:
        recs.append(json.load(fh))
# A paragraph per entry in the heredoc, rewrapped to one line each.
with open(nc_path) as fh:
    paras = [" ".join(b.split()) for b in re.split(r"\n(?=\S)", fh.read().strip()) if b.strip()]
doc = {
    "at": int(os.environ["AT"]),
    "version": os.environ["VERSION"],
    "mode": os.environ["MODE_NAME"],
    "checked": ["%s -- %s" % (r["gateway"], r["what"])
                for r in recs if r["status"] in ("clean", "drift")],
    "drift": [{"gateway": r["gateway"], "what": r["what"], "settings": r["settings"]}
              for r in recs if r["status"] == "drift"],
    "notCompared": paras,
    "errors": ["%s -- %s: %s" % (r["gateway"], r["what"], "; ".join(r["settings"]))
               for r in recs if r["status"] == "error"],
    "skipped": [r["gateway"] for r in recs if r["status"] == "skipped"],
}
with open(out, "w") as fh:
    json.dump(doc, fh, indent=1)
    fh.write("\n")
PY
mv "$STATE.tmp" "$STATE"

if [ "$JSON" -eq 1 ]; then
  cat "$STATE" >&3
else
  show
  dim "  recorded in ${STATE#$REPO_ROOT/} -- print it again for free: scripts/drift.sh --show"
fi

# LOOK reports the answer in its exit status; --show never does. See the header.
if [ "$drifted" -ne 0 ]; then exit 1; fi
exit 0
