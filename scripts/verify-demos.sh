#!/usr/bin/env bash
#
# Is this stack ready to DEMONSTRATE? Run it before walking into a meeting.
#
#   scripts/verify-demos.sh                      check the demos you have started
#   DEMO=eam,redundancy scripts/verify-demos.sh  check just those, whatever is up
#   DEMO=all scripts/verify-demos.sh             check every demo in demos.json
#   scripts/verify-demos.sh --quick              skip the checks that cost a few seconds
#   scripts/verify-demos.sh --json --quick DEMO=eam   one demo, as a document
#
# IT CHECKS WHAT YOU ASKED FOR, NOT WHAT EXISTS
#
# This used to assume the whole stack was up, which was true when the whole
# stack always was. Since the demo console it is not: nine stacks and about
# 6 GB sit stopped until you start a demonstration. Run against a core-only
# machine, the old shape reported eleven failures and advised `./wd up` -- start
# everything -- which is exactly the habit the console was built to break, and a
# readiness tool that cries wolf is a readiness tool people stop reading.
#
# So the set of demos being checked comes from wd-control: the ones you asked
# for. DEMO= overrides it for the machine where the console itself is the thing
# that is broken. The CORE is always checked, because a broken core is not a
# demonstration you can start.
#
# WHY THIS EXISTS, AND WHY IT CHECKS WHAT IT CHECKS
#
# `make status` answers "is it running". Every container can be running and
# healthy while the thing you are about to show a customer is broken -- that is
# not hypothetical, it is what happened on 07/08/2026 on BOTH machines at once.
#
# The Gateway Network edge's live values had been dead since each machine was
# rebuilt from scratch. The remote tag provider that carries them had been made
# by hand in the gateway UI and no script recreated it, so it did not survive.
# The Site card read OFFLINE with three dashes for days and nobody's check
# noticed, because every check counted stored history rows -- and history
# arrives by a completely separate mechanism that was still working perfectly.
# The trend drew both edges' lines the whole time.
#
# So the rule this file is built on: **check the thing the audience looks at,
# not the thing that is easy to count.** Where a demo has two mechanisms behind
# one card, check both of them separately, because either one alone will happily
# report success for a demo that is half dead.
#
# It is READ-ONLY. It fixes nothing and restarts nothing -- each failure names
# the script that repairs it, so the decision to change a running stack stays
# with you. Everything derives from stacks/*/stack.meta, so a gateway added
# later is checked with no edit here.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

QUICK=0
JSON=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --json)  JSON=1 ;;
    # The same variable, as an argument. It is documented above as an
    # environment variable and that is still the shape wd-control uses, but
    # `verify-demos.sh --json --quick DEMO=eam` is how the command reads
    # wherever it is written down -- and answering a copied command line with
    # "unknown argument: DEMO=eam" teaches nobody anything.
    DEMO=*)  DEMO="${arg#DEMO=}" ;;
    "") ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

# In --json mode the DOCUMENT is stdout and everything else is stderr, so
# `verify-demos.sh --json 2>/dev/null` is exactly parseable. Done once, here, by
# moving the real stdout to fd 4 and pointing stdout at stderr -- rather than by
# redirecting each print. `say`, `ok` and `dim` come from lib.sh and are shared
# with every other script, so they must not learn about this mode.
if [ "$JSON" -eq 1 ]; then exec 4>&1 1>&2; else exec 4>&1; fi

need_docker
cd "$REPO_ROOT"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

FAILED=0
FAILURES=""

# Which demonstration the checks below belong to. Set at each section boundary
# and read only by `record` -- it exists so `--json` can attribute a result to a
# demo, which is what lets the Demos page show readiness per card. Kept as an
# explicit assignment rather than derived from `say`, because one section (the
# style pack) legitimately spans core checks and then the EAM edge pushes, and a
# derived value would put the pushes under the wrong heading.
SECTION=core

# Every result, as TSV, for --json. TSV and not JSON-per-line on purpose: quoting
# a shell variable into valid JSON by hand is a bug waiting for the first
# apostrophe, and every one of these messages is prose. python3 does the
# escaping at the end, once.
record() {  # record <ok|fail> <message> <fix>
  [ "$JSON" -eq 1 ] || return 0
  printf '%s\t%s\t%s\t%s\n' "$SECTION" "$1" "$2" "${3:-}" >> "$WORK/results.tsv"
}

# `set -e` is on, so every check has to be written so a FAILING check is not a
# fatal command. Hence the if/then shape throughout rather than `cmd || fail`.
pass() {
  record ok "$*" ""
  printf '%s  ok%s %s\n' "$C_GRN" "$C_RESET" "$*"
}
fail() {
  FAILED=$((FAILED + 1))
  FAILURES="$FAILURES
  $1
      fix: $2"
  record fail "$1" "$2"
  printf '%sFAIL%s %s\n' "$C_RED$C_BOLD" "$C_RESET" "$1" >&2
  printf '%s     fix: %s%s\n' "$C_DIM" "$2" "$C_RESET" >&2
}

api() {  # api <stanza> <path>   -- body only, empty on failure
  node scripts/ign-gw.js api --gateway "$1" --path "$2" 2>/dev/null | tail -1 || true
}

# The hub historian's tables must ignore the duplicate rows the edges' Rolling
# History Buffer replays, and hold EdgeNotice's long strings, on every partition
# -- including the one Ignition creates next month. Either failure aborts whole
# history batches while every status reads Good (scripts/pg-history-guard.sh).
check_history_guard() {
  gateway_running postgres || return 0
  local st
  st="$(docker exec postgres psql -X -tA -F'|' -U postgres -d ignition -c "
    select coalesce((select 'on' from pg_event_trigger where evtname = 'wd_sqlt_partition_guard'
                      and evtenabled <> 'D'), 'MISSING'),
           count(*) filter (where not exists (select 1 from pg_trigger t
                             where t.tgrelid = c.oid and t.tgname = 'wd_skip_duplicate')
                         or (select atttypid from pg_attribute a where a.attrelid = c.oid
                              and a.attname = 'stringvalue') <> 'text'::regtype),
           count(*)
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where c.relkind = 'r' and n.nspname = 'public' and c.relname ~ '^sqlt_data_[0-9]+_'" 2>/dev/null || true)"
  IFS='|' read -r trig unguarded total <<EOF
$st
EOF
  if [ -z "$st" ]; then
    warn "could not read the historian's partitions -- the history guard was not checked"
  elif [ "$trig" != on ] || [ "${unguarded:-1}" -ne 0 ]; then
    fail "history tables are not duplicate-tolerant (event trigger: $trig, $unguarded of $total partitions unguarded) -- a replay or a long EdgeNotice aborts a whole history batch" \
         "./wd -- scripts/pg-history-guard.sh"
  else
    pass "history guard: all $total sqlt_data partitions skip duplicates and take long strings; new ones will too"
  fi
}

# Is this edge on the MQTT-only network? Off it, it cannot reach the broker --
# which is what the console's cut does on purpose, so the fix says how to undo it.
check_on_mqtt_net() {  # check_on_mqtt_net <container>
  if docker inspect -f '{{json .NetworkSettings.Networks}}' "$1" 2>/dev/null | grep -q "\"$MQTT_NET\""; then
    pass "$1 is on $MQTT_NET, where the hub pair's broker answers"
  else
    fail "$1 is NOT on $MQTT_NET -- it cannot reach the broker (cut, or created before the network was in its compose file)" \
         "docker network connect $MQTT_NET $1   (or ./wd up STACK=$1 to recreate it)"
  fi
}

HUB="$(gateways_with_role hub | head -1)"
[ -n "$HUB" ] || die "no hub in the manifests"
HUB_STANZA="$(stanza_for "$HUB")"

# --- which demonstrations are we checking? -----------------------------------
ALL_DEMOS="$(python3 -c '
import json
print(" ".join(d["id"] for d in json.load(open("demos.json"))["demos"]))')"

if [ -n "${DEMO:-}" ]; then
  if [ "$DEMO" = all ]; then
    WANTED="$ALL_DEMOS"; WANTED_FROM="every demo"
  else
    WANTED="$(printf '%s' "$DEMO" | tr ',' ' ')"; WANTED_FROM="DEMO=$DEMO"
  fi
elif RAW_WANTED="$(control_wanted)"; then
  WANTED="$(printf '%s' "$RAW_WANTED" | tr '\n' ' ')"
  WANTED_FROM="asked for"
  [ -n "${WANTED// /}" ] || WANTED_FROM="no demo started -- checking the core alone"
else
  # No control plane, so no way to know what you meant. Over-reporting is the
  # safe direction for a readiness tool -- it will not pass a demo nobody
  # looked at -- and wd-control being unreachable is itself a core failure,
  # which the next section is about to say out loud.
  WANTED="$ALL_DEMOS"; WANTED_FROM="every demo (wd-control is not answering)"
fi

for d in $WANTED; do
  case " $ALL_DEMOS " in
    *" $d "*) ;;
    *) die "no such demo: $d
    demos.json has: $ALL_DEMOS" ;;
  esac
done

# Which stacks that set needs, and -- for anything missing -- the command that
# starts it. A wanted stack that is down is not fixed by `./wd up` any more; it
# is fixed by starting the demo that wants it, which is a different sentence.
python3 - "$WANTED" > "$WORK/wanted.txt" <<'PY'
import json, sys
d = json.load(open("demos.json"))
want = sys.argv[1].split()
core = list(d["core"]["stacks"])
owners = {}
for s in core:
    owners[s] = "make up STACK=%s" % s
for x in d["demos"]:
    if x["id"] in want:
        for s in x["stacks"]:
            owners.setdefault(s, "make demo-start DEMO=%s" % x["id"])
for s in sorted(owners):
    print("%s|%s" % (s, owners[s]))
PY
WANTED_STACKS=" $(cut -d'|' -f1 "$WORK/wanted.txt" | tr '\n' ' ') "

wanted_stack() { case "$WANTED_STACKS" in *" $1 "*) return 0 ;; esac; return 1; }
fix_for()      { sed -n "s/^$1|//p" "$WORK/wanted.txt" | head -1; }

# Sections check one demo each. Asking demos.json whether the id still exists
# means a renamed demo fails loudly here rather than quietly switching a whole
# section off -- a check that silently stops running is worse than no check.
wants() {  # wants <demo-id>
  case " $ALL_DEMOS " in
    *" $1 "*) ;;
    *) die "verify-demos.sh checks a demo called '$1', which demos.json no longer has" ;;
  esac
  case " $WANTED " in *" $1 "*) return 0 ;; esac
  return 1
}
skipped() { dim "  $1 is not being checked -- skipped"; }

# --- 1. every stack up and healthy -------------------------------------------
# The release first. A readiness report is a claim about a particular tree, and
# without the version the claim cannot be repeated on another machine or
# compared with one -- which is exactly what happens when a demonstration works
# here and not there.
printf '%sstack %s%s\n' "$C_BOLD" "$(stack_version)" "$C_RESET"
SECTION=core
say "containers ($WANTED_FROM)"
for s in "${STACK_ORDER[@]}"; do
  wanted_stack "$s" || continue
  state="$(docker inspect -f '{{.State.Running}}' "$s" 2>/dev/null || echo missing)"
  if [ "$state" != "true" ]; then
    fail "$s is not running" "$(fix_for "$s")"
    continue
  fi
  # No healthcheck is not a failure -- some images ship without one.
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$s" 2>/dev/null || echo none)"
  case "$health" in
    healthy|none) pass "$s" ;;
    # Not ready is still not ready, so this fails -- but it is the state every
    # stack passes through for its first minute, and this tool is now most often
    # run straight after starting a demo. "docker logs" is the wrong advice for
    # a container that is doing exactly what it should.
    starting) fail "$s is still starting" "give it a minute, then re-run this" ;;
    *) fail "$s is $health" "docker logs $s --tail 50" ;;
  esac
done

# Not a failure -- you are allowed to start a stack by hand -- but it is RAM,
# and this is the tool people run in the five minutes before a meeting.
EXTRA=""
for s in "${STACK_ORDER[@]}"; do
  if ! wanted_stack "$s" \
     && [ "$(docker inspect -f '{{.State.Running}}' "$s" 2>/dev/null || echo false)" = "true" ]; then
    EXTRA="$EXTRA $s"
  fi
done
[ -z "$EXTRA" ] || dim "  also up, and needed by none of these demos:$EXTRA"

# --- 2. trials ---------------------------------------------------------------
# Unlicensed gateways run a rolling 2-hour trial. 8.3.9 resets only one that has
# expired, so every gateway lapses every two hours until someone resets it
# (docs/TRIALS.md). Little time left is only reported, so a demo can be timed
# around the lapse; EXPIRED is a failure.
SECTION=core
say "Perspective trials"
for gw in "${GATEWAYS[@]}"; do
  wanted_stack "$gw" || continue
  # A gateway that is not running has already been reported once, and asking it
  # for a trial only turns one honest failure into two.
  gateway_running "$gw" || continue
  url="$(gateway_url "$gw")"
  json="$(curl -s --max-time 5 "$url/data/api/v1/trial" 2>/dev/null || true)"
  mins="$(printf '%s' "$json" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1); raise SystemExit
print(-1 if d.get("expired") else int(d.get("trialSecondsLeft", 0)) // 60)
' 2>/dev/null || echo -1)"
  fix_low="reset it at https://console.test/_wd/trials (or ./wd trial-reset GATEWAY=$gw)"
  if [ "$mins" -lt 0 ]; then
    fail "$gw trial is EXPIRED or unreadable" "$fix_low"
  elif [ "$mins" -lt 30 ]; then
    pass "$gw ${mins}m -- lapses in ${mins}m; reset it then at https://console.test/_wd/trials"
  else
    pass "$gw ${mins}m"
  fi
done

# --- 3. styling and EAM distribution -----------------------------------------
# The demo is that one pack choice reaches every project on the hub AND both
# edges. The edges are the half that silently fails: EAM's Send Project is
# differential and compares resource METADATA, so a push reports Success and
# sends nothing when the timestamp was not bumped. Compare the VALUE on each
# edge, never the task result.
SECTION=core
say "style pack -- the same one everywhere"
PACK_FILE="ignition/script-python/demo_styles/code.py"
pack_of() {  # pack_of <container> <project>
  docker exec "$1" sh -c \
    "grep '^CHOSEN_PACK' /usr/local/bin/ignition/data/projects/$2/$PACK_FILE 2>/dev/null" \
    2>/dev/null | head -1 | sed 's/.*= *//; s/\"//g; s/[[:space:]]*$//' || true
}

HUB_PACK=""
for proj in $(docker exec "$HUB" sh -c \
      "ls -d /usr/local/bin/ignition/data/projects/*/$PACK_FILE 2>/dev/null" 2>/dev/null \
      | sed 's|.*/projects/||; s|/.*||' || true); do
  p="$(pack_of "$HUB" "$proj")"
  if [ -z "$p" ]; then
    fail "$HUB project $proj has no readable CHOSEN_PACK" "./wd deploy PROJECT=$proj"
  elif [ -z "$HUB_PACK" ]; then
    HUB_PACK="$p"; pass "$proj = $p"
  elif [ "$p" != "$HUB_PACK" ]; then
    fail "$HUB project $proj is on '$p' but $HUB_PACK elsewhere" "re-pick the pack on /admin"
  else
    pass "$proj = $p"
  fi
done
[ -n "$HUB_PACK" ] || fail "no project on $HUB carries demo_styles" "./wd deploy-all"

# An Edge runs exactly one project and it is always called `Edge`. The hub half
# above is core -- it is the gateway the console runs on -- but the push to the
# edges is the EAM demonstration, and it cannot be checked when the edges are
# deliberately stopped.
EAM_EDGES=""
if wants eam; then
  EAM_EDGES="$(gateways_with_role edge || true)"
else
  dim "  eam is not being checked -- the push to the edges is not verified"
fi
SECTION=eam

# THE AGENT'S CONNECTION, not just the value it happens to hold. Comparing packs
# alone passes whenever the edge already carries the same one -- which is the
# normal state right after a rebuild, when every gateway is on the repo default.
# Measured 31/08/2026: edge2's EAM agent had been Disconnected for three days
# (this VM suspends overnight and the websocket does not survive it), every push
# to it failed with "Agent 'Ignition-Edge2' is currently not connected", and
# this section reported ok on both edges throughout. The same lesson the store
# and forward road already cost us: where a demo has two mechanisms behind one
# card, check both, because either alone reports success for a demo that is half
# dead. The repair is a restart of the edge -- the agent redials on start.
if [ -n "$EAM_EDGES" ]; then
  agents="$(api "$HUB_STANZA" /data/eam/api/v1/agents)"
  for e in $EAM_EDGES; do
    agent="$(meta_get "$e" EAM_AGENT "")"
    [ -n "$agent" ] || continue
    state="$(printf '%s' "$agents" | python3 -c '
import json, sys
want = sys.argv[1]
try:
    items = json.load(sys.stdin).get("items", [])
except Exception:
    print("unreadable"); raise SystemExit
for a in items:
    if a.get("name") == want:
        print(a.get("statusMessage", "?")); raise SystemExit
print("not registered")' "$agent" 2>/dev/null || echo unreadable)"
    if [ "$state" = "Connected" ]; then
      pass "$e agent $agent Connected"
    else
      fail "$e's EAM agent is $state -- every push to it will fail" \
           "docker restart $e"
    fi
  done
fi

# An edge that holds the right pack and cannot SHOW it is still a failed demo.
# `visualizationName` in ignition/edge-system-properties decides which module
# gets the edge's single Panel session, and it defaults to VISION -- Perspective
# is then answered "Sessions Exceeded" on a healthy gateway, which reads as a
# licence limit and is one word of config. Checked here because the pack
# comparison below is happy either way.
for e in $EAM_EDGES; do
  # NOT the api() helper: it ends `| tail -1`, which returns the last LINE of the
  # body -- fine for the single-line JSON most routes emit, wrong for this
  # pretty-printed one, which came back as "}" and read as unreadable.
  #
  # And the body is captured BEFORE it is parsed, deliberately. Piping node
  # straight into python puts both in one pipeline, so under `pipefail` a
  # transient failure of the API call makes the pipeline fail AFTER python has
  # already printed -- the `|| echo` then appends a second line and the check
  # reports a value of "unreadable\nunreadable". Seen once here on a healthy
  # edge whose API answered perfectly a second later.
  raw="$(node scripts/ign-gw.js api --gateway "$(stanza_for "$e")" \
          --path /data/api/v1/resources/singleton/ignition/edge-system-properties \
          2>/dev/null | tail -n +2 || true)"
  vis="$(printf '%s' "$raw" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["config"].get("visualizationName", "?"))
except Exception:
    print("unreadable")' 2>/dev/null)"
  [ -n "$vis" ] || vis=unreadable

  if [ "$vis" = PERSPECTIVE ]; then
    pass "$e Panel session serves Perspective"
  elif [ "$vis" = unreadable ]; then
    # Not a failure of the demo: a failure to ASK. Saying the edge is misconfigured
    # on the strength of a call that did not complete is how a green rig gets
    # "repaired" into a broken one.
    warn "$e -- could not read its Panel visualization (asked at a bad moment?)"
  else
    fail "$e gives its Panel session to '$vis' -- Perspective will answer Sessions Exceeded" \
         "./wd edge-visual"
  fi
done

for e in $EAM_EDGES; do
  p="$(pack_of "$e" Edge)"
  if [ -z "$p" ]; then
    fail "$e has no Edge project carrying the pack" "./wd -- scripts/eam-push.sh $e"
  elif [ -n "$HUB_PACK" ] && [ "$p" != "$HUB_PACK" ]; then
    fail "$e is on '$p', the hub is on '$HUB_PACK'" "./wd -- scripts/eam-push.sh $e"
  else
    pass "$e (pushed) = $p"
  fi
done

# --- 4. store and forward, BOTH roads, BOTH halves of each -------------------
SECTION=store-forward
say "store & forward"

# Gated at the iteration source rather than by wrapping the section in an `if`:
# every check below reaches for a heredoc, and a heredoc terminator has to stay
# in the first column. Empty lists skip the loops just as well and leave the
# checks themselves exactly as they were.
SF_GAN=""; SF_MQTT=""
if wants store-forward; then
  SF_GAN="$(gateways_where SF_TRANSPORT gan || true)"
  SF_MQTT="$(gateways_where SF_TRANSPORT mqtt || true)"
  api "$HUB_STANZA" /data/api/v1/resources/list/ignition/tag-provider > "$WORK/hubtp.json"
else
  skipped store-forward
fi

for e in $SF_GAN; do
  mount="$(meta_get "$e" GAN_PROVIDER)"
  stanza="$(stanza_for "$e")"

  # 4a. LIVE VALUES. This is the check that was missing, and the one the card
  # actually depends on. Comparing the mounted provider's tag count against the
  # edge's own is what proves the mount RESOLVES -- a provider that exists but
  # cannot reach the edge browses zero tags while still reporting enabled.
  api "$stanza" /data/api/v1/resources/list/ignition/tag-provider > "$WORK/edgetp.json"
  read -r hub_n edge_n healthy <<EOF
$(python3 - "$WORK/hubtp.json" "$WORK/edgetp.json" "$mount" <<'PY'
import json, sys
hub, edge, mount = sys.argv[1:4]
def load(p):
    try:
        return json.load(open(p)).get("items", [])
    except Exception:
        return []
def count(i):
    return i.get("metrics", {}).get("tagCount", {}).get("metric", {}).get("value", 0)
m = next((i for i in load(hub) if i["name"] == mount), None)
# edge_stream.PROVIDER -- the standard provider the demo tags live in
src = next((i for i in load(edge)
            if i["config"]["profile"]["type"] == "STANDARD"), None)
print(count(m) if m else -1,
      count(src) if src else -1,
      "yes" if m and m.get("healthchecks", {}).get("status", {})
                 .get("result", {}).get("healthy") else "no")
PY
)
EOF
  if [ "$hub_n" = "-1" ]; then
    fail "$HUB does not mount [$mount] -- $e's live values cannot resolve" \
         "./wd sf-gan-history"
  elif [ "$healthy" != "yes" ]; then
    fail "[$mount] on $HUB is not healthy" "./wd sf-gan-history"
  elif [ "$hub_n" -lt 1 ] || [ "$hub_n" != "$edge_n" ]; then
    fail "[$mount] browses $hub_n tags, $e has $edge_n -- the card will read OFFLINE" \
         "./wd sf-gan-history"
  else
    pass "$e live: [$mount] browses $hub_n tags"
  fi

  # 4b. HISTORY. A faulted sink caches its failure until the historian restarts,
  # so an old refusal in the log is not proof of a current one -- only a recent
  # window is. The historian only restarts on a setting CHANGE, which is why
  # sf-gan-history toggles rather than rewrites.
  #
  # TWO warnings, not one, and that threshold is the whole point. Cutting this
  # edge is a DEMONSTRATION here, and while it is cut the sink correctly logs
  # exactly one refusal -- so a single line means the demo ran, not that the
  # road is broken. Running the cut used to leave this check failing for the
  # next three minutes, which is how a readiness tool teaches you to ignore it.
  # A genuinely faulted sink retries about every 70s and so leaves two or more
  # in the window; a sink that faults just inside it is caught moments later by
  # the row-count check below.
  refusals="$(docker logs "$e" --since 3m 2>&1 | grep -c "isn't accepting data" || true)"
  if [ "${refusals:-0}" -ge 2 ]; then
    fail "$e's historian sink is refusing to forward ($refusals in 3m)" "./wd sf-gan-history"
  elif [ "${refusals:-0}" -eq 1 ]; then
    pass "$e history: sink accepting (one refusal, consistent with a cut)"
  else
    pass "$e history: sink accepting"
  fi
done

TX=com.cirruslink.mqtt.transmission.gateway
for e in $SF_MQTT; do
  gateway_running "$e" || continue
  # The broker road: on wd-mqtt, dialling the hub pair's Distributor and nothing
  # else, with the rolling buffer that makes a hub handover lossless.
  check_on_mqtt_net "$e"
  api "$(stanza_for "$e")" "/data/api/v1/resources/list/$TX/server" > "$WORK/$e.srv.json"
  api "$(stanza_for "$e")" "/data/api/v1/resources/list/$TX/history-store" > "$WORK/$e.hs.json"
  read -r urls rolling <<EOF
$(python3 - "$WORK/$e.srv.json" "$WORK/$e.hs.json" <<'PY' 2>/dev/null || echo "? ?"
import json, sys
def items(p):
    try:
        return json.load(open(p)).get("items", [])
    except Exception:
        return None
srv, hs = items(sys.argv[1]), items(sys.argv[2])
urls = ",".join(i["config"].get("url", "?") for i in (srv or []) if i.get("enabled", True)) or "?"
st = next((i for i in (hs or []) if i.get("name") == "Default In-Memory Store"), None)
print(urls, "?" if st is None else ("on" if st["config"].get("rollingHistoryBufferEnabled") else "off"))
PY
)
EOF
  case "$urls" in
    ssl://mqtt-master:8883,ssl://mqtt-backup:8883) pass "$e dials the hub pair's Distributor: $urls" ;;
    "?") warn "$e -- could not read its Transmission servers" ;;
    *) fail "$e's Transmission servers are $urls, not the hub pair's Distributor" "./wd -- scripts/ign-mqtt.sh setup $e" ;;
  esac
  case "$rolling" in
    on) pass "$e rolling history buffer on (a hub handover replays, loses nothing)" ;;
    "?") warn "$e -- could not read its history store" ;;
    *) fail "$e has no rolling history buffer -- messages sent into a standing-down broker are lost" "./wd sf-arm" ;;
  esac
done
[ -z "$SF_MQTT" ] || check_history_guard

for e in $SF_MQTT; do
  # The transmitter computes its flush type ONCE, when the client is built. If
  # the history store was disabled at that moment it computes NONE -- which does
  # not mean "unordered", it means DROP THE PAYLOAD, at debug level. Everything
  # else about the gateway looks armed. The most recent line is the live one.
  flush="$(docker logs "$e" --since 24h 2>&1 | grep -o 'historyFlushType=[A-Z]*' | tail -1 || true)"
  case "$flush" in
    historyFlushType=ASYNC) pass "$e buffering: $flush" ;;
    "") fail "$e has not reported a flush type -- is the transmitter connected?" "./wd sf-arm" ;;
    *)  fail "$e is $flush -- it DISCARDS buffered data rather than storing it" "./wd sf-arm" ;;
  esac
done

# 4c. Both roads must actually be landing rows at the hub, and each arrives
# under its own driver row. Partition names come from sqlth_partitions rather
# than being built from the driver id and the month -- one less thing to guess.
if [ "$QUICK" -eq 0 ] && [ -n "$SF_GAN$SF_MQTT" ]; then
  # Pipe-separated, NOT space: a driver row with an empty provider (the hub's
  # own historian has one) collapses two spaces into one, and a positional read
  # then lands the partition name in the provider variable and leaves the
  # partition empty -- so every row was skipped and the whole section printed
  # nothing at all, which looks exactly like having no partitions.
  # Only the partition that covers NOW: last month's has no recent rows by
  # definition, and checking it failed every run from the 1st of the month.
  docker exec postgres psql -U postgres -d ignition -t -A -F'|' -c "
    select d.name, coalesce(d.provider,''), p.pname from sqlth_drv d
      join sqlth_partitions p on p.drvid = d.id
     where p.start_time <= extract(epoch from now()) * 1000
       and p.end_time   >  extract(epoch from now()) * 1000" 2>/dev/null > "$WORK/parts.txt" || true
  if [ ! -s "$WORK/parts.txt" ]; then
    fail "no history partitions at the hub at all" "./wd sf-setup"
  else
    # Redirected, not piped: a `while` on the right of a pipe runs in a subshell,
    # so every fail() inside it would increment a counter that is discarded.
    while IFS='|' read -r dname dprov pname; do
      [ -n "${pname:-}" ] || continue
      n="$(docker exec postgres psql -U postgres -d ignition -t -A -c \
            "select count(*) from $pname where t_stamp > (extract(epoch from now())-300)*1000" \
            2>/dev/null || echo 0)"
      label="$dname${dprov:+ / $dprov}"
      if [ "${n:-0}" -lt 1 ]; then
        fail "no rows from $label in the last 5 minutes" "./wd sf-setup"
      else
        pass "$label historising ($n rows/5m)"
      fi
    done < "$WORK/parts.txt"
  fi
fi

# --- 5. redundancy, and the one front door in front of it --------------------
SECTION=redundancy
say "redundancy"
BACKUP="$(gateways_with_role backup | head -1)"
if ! wants redundancy; then
  skipped redundancy
elif [ -z "$BACKUP" ]; then
  dim "  no backup in the manifests -- skipping"
else
  for half in "$HUB" "$BACKUP"; do
    st="$(api "$(stanza_for "$half")" /data/api/v1/redundancy)"
    # SPLIT ON '|', NOT ON WHITESPACE. `projectState` is a human phrase and one
    # of its values is "Out of Date" -- three words, which a whitespace `read`
    # shreds across the remaining fields: `project=Out peer=of Date False`.
    # Nonsense on screen, and `peer` then never equals True, so a pair that was
    # merely mid-sync got reported as a split brain.
    IFS='|' read -r role activity project peer <<EOF
$(printf '%s' "$st" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("?|?|?|?"); raise SystemExit
print("%s|%s|%s|%s" % (d.get("role","?"), d.get("activityLevel","?"),
                       d.get("projectState","?"), d.get("peerConnected","?")))
' 2>/dev/null || echo "?|?|?|?")
EOF
    # PEER BEFORE PROJECT, because project is the field that can contain spaces
    # and awk below reads by column. Putting the multi-word value last means it
    # cannot push anything else out of place.
    printf '%s %s %s %s %s\n' "$half" "$role" "$activity" "$peer" "$project" \
      >> "$WORK/redundancy.txt"
    if [ "$project" != "Good" ] || [ "$peer" != "True" ]; then
      fail "$half: role=$role activity=$activity project=$project peer=$peer" \
           "./wd redundancy-status, then ./wd redundancy"
    else
      pass "$half $role/$activity, project $project"
    fi
  done

  # NAME THE SHAPE, not just the fields. Two failing halves printing
  # `project=Unknown peer=False` are not two problems -- they are one, and it
  # has a name: the halves cannot see each other, so BOTH have taken charge.
  # That is correct behaviour from each of them in isolation (a backup that has
  # lost its master is supposed to go Active) and it is the single worst state
  # to walk into a demonstration with, because each gateway looks healthy on its
  # own page. Reported once, in words, with the repair that actually applies.
  n_active="$(awk '$3 == "Active"' "$WORK/redundancy.txt" | wc -l | tr -d ' ')"
  n_nopeer="$(awk '$4 != "True"'   "$WORK/redundancy.txt" | wc -l | tr -d ' ')"

  # A BACKUP THAT HAS JUST STARTED LOOKS EXACTLY LIKE A SPLIT BRAIN, and saying
  # the frightening thing about the ordinary one is how a check gets ignored.
  # The backup boots, reads redundancy.xml, cannot reach the master yet and
  # takes charge -- correct behaviour, and it is the state anyone pressing
  # "Check readiness" straight after starting the demo will catch. Measured
  # 31/08/2026: the pair re-paired ~30s after the link returned. So the age of
  # the container decides which sentence this is.
  started="$(docker inspect "$BACKUP" --format '{{.State.StartedAt}}' 2>/dev/null || true)"
  age=999
  if [ -n "$started" ]; then
    age="$(python3 - "$started" <<'PY' 2>/dev/null || echo 999
import sys, datetime
t = sys.argv[1][:19]
d = datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%S")
print(int((datetime.datetime.utcnow() - d).total_seconds()))
PY
)"
  fi
  if [ "${n_active:-0}" -ge 2 ] && [ "${n_nopeer:-0}" -ge 1 ] && [ "${age:-999}" -lt 150 ]; then
    warn "the backup started ${age}s ago and has not paired yet -- normal for
       about a minute. Re-check before doing anything: on the Demos page,
       press Check readiness again."
  elif [ "${n_active:-0}" -ge 2 ] && [ "${n_nopeer:-0}" -ge 1 ]; then
    # ONE half reporting the loss is enough. The other's `peerConnected: True`
    # can simply be stale -- measured here, the master still claimed a peer for
    # over a minute after the backup had been cut off the network and gone
    # Active on its own. Waiting for both to agree before saying anything is
    # waiting for the more optimistic of two reports to catch up.
    fail "SPLIT BRAIN: neither half can see the other, so both have taken charge" \
         "./wd redundancy   (re-pairs; safe to re-run). If it does not take, the
       blocker is one of three, in this order -- TLS trust on the backup, the
       incoming connection not approved on the master (websocket 403), or the
       backup restarting to install the master's modules. docs/REDUNDANCY.md
       has the log line for each; 'docker logs ignition-backup --tail 50' shows
       which one you are on."
  fi

  # The proxy picks the half that reports itself ACTIVE, so this is also the
  # check that a planned failover would land anywhere. Ask the proxy, not a
  # gateway: an "is it up" answer from either half proves nothing, because both
  # are up and only one is active.
  if [ "$QUICK" -eq 0 ]; then
    ha="$(meta_get ignition-ha PORT_DEFAULT)"
    gwinfo="$(docker exec ignition-ha sh -c \
      "wget -qO- --header='Host: ignition.test' http://127.0.0.1:8088/system/gwinfo 2>/dev/null" \
      2>/dev/null || true)"
    # `Active` ALONE IS NOT THE CHECK, and believing it was is how this passed
    # on 31/08/2026 while the front door was serving ignition-edge1 and
    # answering `project "GatewayAdmin" does not exist`. A gateway with no
    # redundancy reports Independent AND Active -- from its own point of view it
    # is the one in charge -- so every Ignition container on the network
    # satisfies the Active test. What it must ALSO be is half of the pair.
    #
    # grep -c and a count, not grep -q: an early-exiting grep -q SIGPIPEs its
    # upstream, so the pipeline returns 141 and a real failure reads as none.
    n_active="$(printf '%s' "$gwinfo" | grep -c 'RedundantNodeActiveStatus=Active' || true)"
    n_indep="$(printf '%s' "$gwinfo" | grep -c 'RedundancyStatus=Independent' || true)"
    if [ "${n_indep:-0}" -gt 0 ]; then
      fail "the front door is serving a gateway that is not in the pair" \
           "./wd restart STACK=ignition-ha  (its DNS cache is stale)"
    elif [ "${n_active:-0}" -gt 0 ]; then
      pass "front door is serving the Active half of the pair"
    else
      fail "the front door is not serving an Active gateway" \
           "docker logs ignition-ha --tail 30, and http://localhost:${ha:-8404}"
    fi
  fi
fi

# --- 6. isolated edges: nothing crosses but the broker ------------------------
# ROLE=edge-isolated exists so a gateway can get every ordinary edge treatment
# (modules, edge-visual, public address, sign-in) while being left OUT of
# every Gateway-Network/EAM step -- see stacks/ignition-edge3/stack.meta. That
# is an omission in a handful of scripts, not a positive guarantee, so this is
# the check that actually proves the demo's claim rather than trusting that
# the omission was never undone by hand in the gateway UI (the same trap the
# Gateway Network road's remote tag provider fell into -- see docs/EAM.md).
#
# Unconditional on demos.json/DEMO=: there is no "started" state for a
# topology invariant, only a running container to ask, and this is cheap
# enough to always run rather than gate behind --quick.
SECTION=core
for e in $(gateways_where ROLE edge-isolated); do
  gateway_running "$e" || continue
  raw="$(api "$(stanza_for "$e")" /data/api/v1/resources/list/ignition/gateway-network-outgoing)"
  n="$(printf '%s' "$raw" | python3 -c '
import json, sys
try:
    print(len(json.load(sys.stdin).get("items", [])))
except Exception:
    print(-1)' 2>/dev/null || echo -1)"
  if [ "${n:-0}" -eq 0 ]; then
    pass "$e has no Gateway Network connection -- isolation holds"
  elif [ "${n:-0}" -lt 0 ]; then
    warn "$e -- could not read its Gateway Network connections (asked at a bad moment?)"
  else
    fail "$e has $n outgoing Gateway Network connection(s) configured -- the whole point of this demo is that nothing crosses except the MQTT broker" \
         "remove it: Config > Networking > Gateway Network (Outgoing Connections) on $e -- it was never created by this repo's scripts, so nothing here will remove it for you"
  fi
done

# --- 7. sparkplug: is MQTT Engine consuming what the edges publish? ------------
# Two silent traps, both measured (docs/MQTT-DISTRIBUTOR.md T-D5, T-D10). An
# Engine server whose SERVER SET has no Sparkplug namespace bound connects,
# publishes STATE online and subscribes to STATE alone -- so every edge trusts
# it, publishes live, buffers nothing, and everything it sends is dropped at the
# broker while every status reads Good. And the general form of the same
# failure: an edge that says it is connected while Engine's copy of its data is
# not moving. The first is read from Engine's config; the second compares the
# edge's own `transmission.connected` with the age of Engine's Heartbeat for
# that node, and the broker's own view where there is one (the wire witness's
# last message from the node).
SECTION=sparkplug
say "sparkplug"
if ! wants sparkplug; then
  skipped sparkplug
else
  ENG=com.cirruslink.mqtt.engine.gateway
  for t in server server-set namespace-server-set; do
    node scripts/ign-gw.js api --gateway "$HUB_STANZA" --path "/data/api/v1/resources/list/$ENG/$t" \
      2>/dev/null | sed 1d > "$WORK/eng-$t.json" || true
  done
  python3 - "$WORK/eng-server.json" "$WORK/eng-server-set.json" "$WORK/eng-namespace-server-set.json" \
    > "$WORK/eng-sets.txt" <<'PY' || true
import json, sys
def items(p):
    try:
        return json.load(open(p)).get("items", [])
    except Exception:
        return None
servers, sets, binds = (items(p) for p in sys.argv[1:4])
if servers is None or binds is None:
    print("unreadable"); raise SystemExit
bound = {}
for b in binds:
    c = b.get("config") or {}
    if b.get("enabled", True) and str(c.get("namespace", "")).startswith("Sparkplug"):
        bound.setdefault(c.get("serverSet"), []).append(c.get("namespace"))
for s in servers:
    c = s.get("config") or {}
    if not s.get("enabled", True):
        continue
    ss = c.get("serverSet")
    print("%s|%s|%s|%s" % ("ok" if ss in bound else "unbound", s["name"], ss, c.get("url")))
PY
  if grep -q '^unreadable' "$WORK/eng-sets.txt" || [ ! -s "$WORK/eng-sets.txt" ]; then
    warn "could not read $HUB's MQTT Engine servers -- the namespace-binding check did not run"
  fi
  while IFS='|' read -r verdict server set url; do
    [ -n "$server" ] || continue
    if [ "$verdict" = ok ]; then
      pass "Engine server '$server' ($url): its set '$set' has the Sparkplug B namespace"
    else
      fail "Engine server '$server' ($url) is in set '$set', which has NO Sparkplug namespace bound -- it publishes STATE online and consumes nothing" \
           "bind it: MQTT Engine > Namespaces > Sparkplug B > server sets (resource namespace-server-set, e.g. 'Sparkplug B-$set')"
    fi
  done < <(grep -v '^unreadable' "$WORK/eng-sets.txt" || true)

  # A broker that is an Ignition module is only as alive as that gateway's
  # licence. Distributor starts only on an ACTIVE half (T-D1) and not at all on
  # a lapsed trial (T-D7, T-D14): a broker in the hub makes the standby's trial
  # a Sparkplug readiness item, and a standalone broker gateway's trial is the
  # whole MQTT road.
  for host in $(python3 -c '
import json, sys
from urllib.parse import urlparse
try:
    items = json.load(open(sys.argv[1])).get("items", [])
except Exception:
    items = []
print(" ".join(sorted(set(urlparse(i["config"]["url"]).hostname or "" for i in items
                          if i.get("enabled", True) and (i.get("config") or {}).get("url")))))' \
      "$WORK/eng-server.json" 2>/dev/null); do
    case "$host" in
      localhost|ignition|ignition-backup|mqtt-master|mqtt-backup) broker_gws="$HUB $(gateways_with_role backup)" ;;
      mqtt-broker|ignition-broker)                 broker_gws="ignition-broker" ;;
      *) fail "Engine has a server on $host, which is not the hub pair's Distributor" \
              "./wd -- scripts/ign-mqtt.sh setup $HUB"
         continue ;;
    esac
    for g in $broker_gws; do
      if ! gateway_running "$g"; then
        # The backup is the redundancy demo's; the pair's broker works without
        # it, it just has nowhere to fail over to.
        if [ "$g" != "$HUB" ]; then dim "  $g (the other half's broker) is not running -- no failover target"; continue; fi
        fail "Engine uses a broker in $g ($host), and $g is not running" "./wd up STACK=$g"
        continue
      fi
      # A lapse is routine on 8.3.9 and a person resets it (docs/TRIALS.md);
      # only an expired trial means no broker right now.
      left="$(trial_seconds "$g")"
      if [ "$left" -gt 0 ] 2>/dev/null; then
        pass "$g runs an MQTT broker Engine uses ($host) and its trial has $((left / 60)) min"
      else
        fail "$g runs an MQTT broker Engine uses ($host), and its trial is expired or unreadable -- a lapsed trial starts no broker" \
             "wait 10 s; if it stays expired: ./wd restart STACK=$g"
      fi
    done
  done

  # Ask the ACTIVE half: a standby's Engine consumes nothing by design, and its
  # copy of every Heartbeat is stale for as long as it stands by.
  ACTIVE_HUB="$HUB"
  for g in "$HUB" $(gateways_with_role backup); do
    gateway_running "$g" || continue
    if curl -s --max-time 5 "$(gateway_url "$g")/system/gwinfo" 2>/dev/null \
         | grep -q 'RedundantNodeActiveStatus=Active'; then
      ACTIVE_HUB="$g"; break
    fi
  done
  curl -s --max-time 20 "$(gateway_url "$ACTIVE_HUB")/system/webdev/GatewayAdmin/sparkplug/udt" \
    > "$WORK/sp-udt.json" 2>/dev/null || true
  for e in $(sparkplug_edges); do
    gateway_running "$e" || continue
    curl -s --max-time 10 "$(gateway_url "$e")/system/webdev/Edge/sparkplug/state" \
      > "$WORK/sp-$e.json" 2>/dev/null || true
    IFS='|' read -r node connected hb_age wire_age <<EOF
$(python3 - "$WORK/sp-$e.json" "$WORK/sp-udt.json" <<'PY' 2>/dev/null || echo "?|?|?|?"
import json, sys
try:
    edge = json.load(open(sys.argv[1]))
except Exception:
    print("?|?|?|?"); raise SystemExit
node = edge.get("node") or "?"
connected = (edge.get("transmission") or {}).get("connected")
hb_age = wire_age = "?"
try:
    udt = json.load(open(sys.argv[2]))
    now = udt.get("now")
    for row in udt.get("edges") or []:
        if row.get("node") == node:
            hb = ((row.get("engine") or {}).get("heartbeat") or {})
            if hb.get("ts") and now:
                hb_age = int((now - hb["ts"]) / 1000)
            w = (row.get("wire") or {}).get("lastDataAgoMs")
            if w is not None:
                wire_age = int(w / 1000)
except Exception:
    pass
print("%s|%s|%s|%s" % (node, connected, hb_age, wire_age))
PY
)
EOF
    if [ "$connected" != True ]; then
      fail "$e ($node) says its transmitter is NOT connected to the broker" \
           "docker logs $e --tail 40 | grep -i mqtt, then scripts/sparkplug-setup.sh $e"
    elif [ "$hb_age" = "?" ]; then
      warn "$e ($node) -- could not read Engine's Heartbeat for it from the hub"
    elif [ "$hb_age" -gt 30 ]; then
      fail "$e ($node) is connected, but Engine's copy of its Heartbeat is ${hb_age}s old${wire_age:+ (the broker last saw it ${wire_age}s ago)} -- Engine is not consuming it" \
           "check the Engine server set holding this broker has the Sparkplug B namespace bound (above), and Engine's log for the server"
    else
      pass "$e ($node) connected, and Engine's copy is live (Heartbeat ${hb_age}s old)"
    fi
    check_on_mqtt_net "$e"
    first="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("transmission") or {}).get("server") or "?")' \
              "$WORK/sp-$e.json" 2>/dev/null || echo "?")"
    case "$first" in
      ssl://mqtt-master:8883) ;;
      "?") ;;
      *) fail "$e's first Transmission server is $first, not the hub pair's Distributor (ssl://mqtt-master:8883)" \
              "./wd -- scripts/ign-mqtt.sh setup $e" ;;
    esac
  done

  # The broker's own view: TLS sessions established on 8883 on the ACTIVE half
  # (its Engine, every edge, the console's listener). A standby runs no broker
  # (T-D1), so it should hold none.
  n_edges=0
  for e in $(sparkplug_edges); do gateway_running "$e" && n_edges=$((n_edges + 1)); done
  sessions="$(docker exec "$ACTIVE_HUB" sh -c 'cat /proc/net/tcp /proc/net/tcp6 2>/dev/null' \
                | awk '$2 ~ /:22B3$/ && $4 == "01"' | wc -l || true)"
  if [ "${sessions:-0}" -ge $((n_edges + 1)) ]; then
    pass "$ACTIVE_HUB's Distributor holds $sessions client session(s) on 8883 (Engine + $n_edges Sparkplug edge(s) at least)"
  else
    fail "$ACTIVE_HUB's Distributor holds ${sessions:-0} client session(s) on 8883; expected Engine + $n_edges edge(s)" \
         "./wd -- scripts/distributor-setup.sh --status $ACTIVE_HUB, then scripts/ign-mqtt.sh status"
  fi
  check_history_guard
fi

# --- the machine-readable verdict --------------------------------------------
# Emitted BEFORE the human verdict so stdout in --json mode is the document and
# nothing else. The exit code is unchanged either way: this is the same run,
# reported twice, not a second mode with its own opinion.
if [ "$JSON" -eq 1 ]; then
  SECTION='' WANTED="$WANTED" FAILED="$FAILED" python3 - "$WORK/results.tsv" >&4 <<'PY'
import json, os, sys

rows = []
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                rows.append(parts)
except IOError:
    pass                        # no checks ran at all; still emit a document

by = {}
for section, verdict, message, fix in ((r + [""])[:4] for r in rows):
    d = by.setdefault(section, {"demo": section, "ok": True,
                                "checked": 0, "problems": []})
    d["checked"] += 1
    if verdict != "ok":
        d["ok"] = False
        d["problems"].append({"what": message, "fix": fix})

print(json.dumps({
    # What was ASKED for, so a reader can tell "ready" from "ready, of the two
    # things anybody looked at". A pass on a core-only machine says nothing
    # about store & forward, and the page has to be able to say which.
    "wanted": os.environ.get("WANTED", "").split(),
    "failed": int(os.environ.get("FAILED") or 0),
    "sections": [by[k] for k in sorted(by)],
}))
PY
fi

# --- where to go now ---------------------------------------------------------
#
# A green verdict and no way to reach the thing it just checked is a small
# cruelty: the next question after "is it ready?" is always "where is it?", and
# the answer is a URL nobody memorises -- the console is a TAB inside a
# Perspective project, not a route, so it is
# `/data/perspective/client/GatewayAdmin` rather than anything guessable.
#
# BOTH FORMS, because neither is right everywhere. The `.test` name is the
# nicer one and needs this machine's hosts file (`make hosts`, which `wd` runs
# for you); the host-port one always works locally. And on a VM you are very
# likely browsing from somewhere else -- where `localhost` means the wrong
# machine and the `.test` names do not resolve at all -- so it says so rather
# than printing a link that silently points at the reader's own laptop.
# A LINK FOR A HUMAN IS ALWAYS THE HOST FORM, so this reads `.hosturl` itself
# rather than calling gateway_url(). That helper is right and this is the one
# case it is right about the wrong thing: inside the toolbox it returns the
# CONTAINER address, because that is what a script in there must use -- and
# `http://ignition:8088` printed as a link resolves nowhere in the browser of
# the person reading it. `make verify-demos` runs in the toolbox, so that is
# the normal case, not the edge one. Caught by running it.
console_link() {
  # The stanza is not the container name -- the hub is `local`, not `ignition`.
  # stanza_for() owns that mapping; hard-coding either name is how a lookup
  # quietly misses and prints nothing at all, which is what the first attempt
  # at this did.
  local stanza host_url
  stanza="$(stanza_for ignition 2>/dev/null || echo local)"
  host_url="$(grep -E "^${stanza}\.hosturl=" "$GATEWAYS_FILE" 2>/dev/null \
              | head -1 | cut -d= -f2- || true)"
  host_url="${host_url%/}"
  # THE PAIR'S FRONT DOOR FIRST, not console.test. Both reach the console, and
  # only one of them survives a failover: `console.test` is a proxy host pointed
  # at the HUB, so a session opened there dies the moment the hub hands over --
  # in the middle of the demonstration that exists to show it not dying.
  # `ignition.test` is HAProxy in front of both halves and follows whichever is
  # active. Printing the fragile one at the top of the readiness report was
  # inviting exactly the failure the report is meant to prevent.
  local front
  front="$(meta_get ignition PUBLIC_HOST ignition.test)"
  echo
  dim "  the demo console:"
  printf '    %shttps://%s/data/perspective/client/GatewayAdmin%s   (follows a failover)\n' \
    "$C_BOLD" "$front" "$C_RESET"
  dim "    https://console.test/data/perspective/client/GatewayAdmin   (this hub only)"
  # `if`, not `[ -n ... ] && printf`: a && list whose test fails IS the whole
  # statement, so under `set -e` a .gateways.env without a hosturl would end the
  # run here -- silently, at the last line of a report that had passed.
  if [ -n "$host_url" ]; then
    printf '    %s/data/perspective/client/GatewayAdmin   (this machine only)\n' "$host_url"
  fi
  dim "    From ANOTHER machine: swap localhost for this box's address, and use"
  dim "    the port form -- the .test names live in THIS machine's hosts file."
  # The moment before a demonstration is exactly when you want to know the
  # machine is a fortnight behind, and it is also the last moment you want a
  # script to go and fetch something. This prints the RECORDED answer only.
  "$REPO_ROOT/scripts/update-check.sh" --show || true
  # And the same for settings somebody changed by hand: an update would put
  # them back, so knowing before a demonstration is worth a file read. RECORDED
  # only -- `make drift` is what looks, and it costs minutes.
  if [ -x "$REPO_ROOT/scripts/drift.sh" ]; then
    "$REPO_ROOT/scripts/drift.sh" --show || true
  fi
}

# --- verdict -----------------------------------------------------------------
echo
if [ "$FAILED" -eq 0 ]; then
  # Named, because "ready" is only ever a claim about what was checked. A pass
  # on a core-only machine says nothing about the store-and-forward demo, and
  # printing "every demonstration is ready" there would be a lie of scope.
  if [ -z "${WANTED// /}" ]; then
    ok "the core is ready -- no demo has been started (make demos)"
  else
    ok "ready:$(printf '%s' " $WANTED" | sed 's/  */ /g')"
  fi
  console_link
  exit 0
fi
# All on one stream: the per-check FAILs go to stderr as they happen, and a
# summary interleaved across two streams arrives in an order nobody can read.
printf '%s%d check(s) failed:%s\n' "$C_RED$C_BOLD" "$FAILED" "$C_RESET"
printf '%s\n' "$FAILURES"
echo
dim "  Nothing here changes the stack. Run the named fix, then re-run this."
exit 1
