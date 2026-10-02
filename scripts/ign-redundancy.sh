#!/usr/bin/env bash
#
# Pair the hub gateway with its redundant backup, and drive the demonstration.
#
#   scripts/ign-redundancy.sh setup      configure both roles and wait for Good
#   scripts/ign-redundancy.sh status     what each half reports, side by side
#   scripts/ign-redundancy.sh check      would `setup` CHANGE either half's
#                                        settings? Reports; PUTs nothing
#   scripts/ign-redundancy.sh failover   hand responsibility to the peer
#   scripts/ign-redundancy.sh resync     force a full configuration sync
#   scripts/ign-redundancy.sh unpair     put both halves back to Independent
#
# Idempotent: `setup` re-reads both sides first and writes only what differs,
# so it is safe on every bootstrap.
#
# WHY THIS IS A SCRIPT AND NOT A DOCUMENTED CLICK-PATH
#
# Redundancy is the one piece of gateway state that is genuinely file-based --
# data/redundancy.xml, a plain Java properties file -- and it is still wrong to
# write it directly: the gateway reads it at startup and on save, so a hand-
# edited file needs a restart to take effect, while the REST route below applies
# it LIVE. Verified 04/08/2026: a PUT flipped a running gateway from Independent
# to Backup in under a second, module sync and all, with no restart anywhere.
#
#   GET  /data/api/v1/redundancy                 status  (role, level, peer)
#   GET  /data/api/v1/redundancy/config          settings
#   PUT  /data/api/v1/redundancy/config          settings, applied immediately
#   GET  /data/api/v1/redundancy/providers       per-subsystem sync metrics
#   GET  /data/api/v1/redundancy/events          the redundancy log
#   POST /data/api/v1/redundancy/gwaction/failover
#   POST /data/api/v1/redundancy/gwaction/resync
#
# Those last two are `gwaction`, not the bare verb. `/redundancy/failover` and
# `/redundancy/resync` both 404, which reads exactly like the route not existing
# in this version -- the path is in RedundancyRoutes in gateway-8.3.8.jar.
#
# THREE THINGS THAT BLOCK THE PAIR, IN THE ORDER YOU HIT THEM
#
#   1. TLS trust.   Redundancy rides the Gateway Network transport with SSL on,
#      so each half must approve the other's certificate. The backup fails first
#      with `InvalidAlgorithmParameterException: the trustAnchors parameter must
#      be non-empty` (its trust store is EMPTY -- not a rejected cert), and once
#      that is fixed the MASTER rejects the backup with `Received fatal alert:
#      certificate_unknown`. Two separate approvals, one per direction, and the
#      second only becomes possible after the first: the cert has to arrive
#      before it can be trusted. That is why the loop below runs ign-gan.sh
#      twice with a wait between.
#
#   2. Connection approval.  A trusted certificate is not an approved
#      connection. Until the incoming connection is approved the handshake dies
#      with `UpgradeException: ... 403 Forbidden`, which reads like a
#      credentials problem and is not one.
#
#   3. Module parity.  The backup then reports `Incompatible (Modules)` and
#      RESTARTS ITSELF to install the master's third-party modules. This is
#      Ignition doing the right thing unprompted -- Embr Charts, the Architecture
#      Builder and MQTT Engine all arrived on a bare backup with no certificate
#      wizard and no intervention -- but it means `setup` has to tolerate the
#      backup disappearing for ~60s in the middle. Do not read that restart as a
#      failure.
#
# The pair share ONE gateway system name: on the first sync the backup takes the
# master's name with its role appended (Ignition-Standard -> the backup reports
# Ignition-Standard-Backup). See stacks/ignition-backup/compose.yaml for why
# that means the backup carries no `-n` argument.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MASTER_CONTAINER="ignition"
BACKUP_CONTAINER="ignition-backup"
MASTER_STANZA="$(stanza_for "$MASTER_CONTAINER")"
BACKUP_STANZA="$(stanza_for "$BACKUP_CONTAINER")"

# The backup dials the master by CONTAINER NAME on the shared backbone network,
# never by a host port. The host mappings (8090, 8388, ...) differ per machine
# and are for a browser on that machine; the pair must not depend on them or the
# stack stops being portable.
MASTER_GAN_HOST="$MASTER_CONTAINER"
MASTER_GAN_PORT=8060

# Warm, not Cold: a cold backup loads nothing until it is needed, so the first
# failover in a demonstration would spend a minute starting projects while a
# customer watched. Warm keeps the projects loaded and idle, which is both the
# realistic production choice and the one that makes the demo instant.
STANDBY_LEVEL=Warm

# Automatic: when the master comes back it takes responsibility again on its
# own. Manual is the other half of the demonstration and is set from the page,
# not from here -- see docs/REDUNDANCY.md.
RECOVERY_MODE=Automatic

# The one hostname in front of BOTH halves. A standby serves no Perspective
# session: it redirects the client to the active node's PUBLIC ADDRESS, so
# unless both halves advertise the same origin the browser blocks the redirect
# and the session hangs at "Connecting" forever. Setting it is therefore part of
# pairing, not a separate piece of polish -- and it is NOT synchronised by
# redundancy, so each half has to be told separately.
#
# It is now written by scripts/ign-public-address.sh, from PUBLIC_HOST in each
# gateway's stack.meta -- because the pair were the only gateways anyone ever
# set it on, which left both EDGES advertising a docker-internal address and
# their Launch Perspective buttons leading nowhere.
PUBLIC_ADDRESS="$(meta_get ignition PUBLIC_HOST ignition.test)"

CMD="${1:-status}"

need_docker
cd "$REPO_ROOT"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

api() {  # api <stanza> <method> <path> [body-file]
  local stanza="$1" method="$2" path="$3" body="${4:-}" rc
  # ign-gw.js now times out every fetch IT issues on its own (FETCH_TIMEOUT_MS
  # there) -- `timeout` here is the belt to that braces, catching anything the
  # fetch-level fix does not: a wedged browser launch, a zombie Chromium. A
  # polling loop checks its own timeout only between calls, so one call that
  # never returns is not one it can bound. 45s is well above a normal call.
  if [ -n "$body" ]; then
    timeout 45 node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" --body-file "$body" 2>&1
    rc=$?
  else
    timeout 45 node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" 2>&1
    rc=$?
  fi
  if [ "$rc" = 124 ]; then
    echo "$stanza: $method $path -- gave up after 45s with no answer" >&2
  fi
  return "$rc"
}

# Tell wd-control's changeover recorder (control/redproof.py) that a handover
# or stop was asked for, so the page times a planned one from the request.
# Best effort: the recorder still sees the changeover without it.
note_request() {
  curl -s -m 3 -X POST -H 'Content-Type: application/json' \
    -d "{\"kind\":\"$1\"}" "$(control_base)/redundancy/request" >/dev/null 2>&1 || true
}

# The api helper prints a status line then the body; the body is the last line.
body() { tail -1; }

state_of() {  # state_of <stanza> -> role|activity|project|peerConnected|peerId
  api "$1" GET /data/api/v1/redundancy | body | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("?|?|?|?|"); raise SystemExit(0)
print("%s|%s|%s|%s|%s" % (d.get("role","?"), d.get("activityLevel","?"),
                          d.get("projectState","?"), d.get("peerConnected",False),
                          d.get("peerId","") or ""))'
}

show_status() {
  local half stanza container s
  printf '%-10s %-12s %-10s %-22s %-6s %s\n' HALF ROLE ACTIVITY PROJECT PEER "PEER NAME"
  for half in master backup; do
    if [ "$half" = master ]; then stanza="$MASTER_STANZA"; container="$MASTER_CONTAINER"
    else                          stanza="$BACKUP_STANZA"; container="$BACKUP_CONTAINER"; fi
    if ! gateway_running "$container"; then
      printf '%-10s %s\n' "$half" "container '$container' is not running"
      continue
    fi
    s="$(state_of "$stanza")"
    printf '%-10s %-12s %-10s %-22s %-6s %s\n' "$half" \
      "$(echo "$s" | cut -d'|' -f1)" "$(echo "$s" | cut -d'|' -f2)" \
      "$(echo "$s" | cut -d'|' -f3)" "$(echo "$s" | cut -d'|' -f4)" \
      "$(echo "$s" | cut -d'|' -f5)"
  done
}

# Write one half's role, but only if it is not already what we want. Rewriting
# an identical config is not free: applySettings restarts the gateway network
# channel, which drops the redundancy link for ~30s. On a re-run of bootstrap
# that would tear down a working pair to arrive at the same settings.
set_role() {  # set_role <stanza> <Master|Backup|Independent> <gan-host>
  local stanza="$1" role="$2" host="$3"
  api "$stanza" GET /data/api/v1/redundancy/config | body > "$WORK/$stanza.json"

  python3 - "$WORK/$stanza.json" "$WORK/$stanza.put.json" \
           "$role" "$host" "$MASTER_GAN_PORT" "$STANDBY_LEVEL" "$RECOVERY_MODE" <<'PY'
import json, sys
src, dst, role, host, port, standby, recovery = sys.argv[1:8]
c = json.load(open(src))
before = json.dumps(c, sort_keys=True)
c["role"] = role
c["standbyActivityLevel"] = standby
c["masterRecoveryMode"] = recovery
# gatewayNetworkSetup applies to the BACKUP only -- the gateway's own OpenAPI
# description says so. Setting it on the master is harmless but pointless: the
# master never dials out, it is dialled into.
c.setdefault("gatewayNetworkSetup", {})
c["gatewayNetworkSetup"]["host"] = host
c["gatewayNetworkSetup"]["port"] = int(port)
json.dump(c, open(dst, "w"), indent=1)
# WHICH settings differ, for the `check` verb to name in plain words. A SIDE
# FILE, not a second line on stdout: pair() and unpair compare this function's
# whole output to the string "changed", so an extra line would quietly stop
# matching and `setup` would skip every write it ought to make.
orig = json.loads(before)
names = {"role": "role", "standbyActivityLevel": "standby activity level",
         "masterRecoveryMode": "master recovery mode"}
diff = ["%s is %r, not %r" % (names[k], orig.get(k), c.get(k))
        for k in names if orig.get(k) != c.get(k)]
og = orig.get("gatewayNetworkSetup") or {}
ng = c["gatewayNetworkSetup"]
if og.get("host") != ng["host"] or og.get("port") != ng["port"]:
    diff.append("Gateway Network target is %s:%s, not %s:%s"
                % (og.get("host"), og.get("port"), ng["host"], ng["port"]))
open(dst + ".diff", "w").write("; ".join(diff) + "\n")
print("changed" if json.dumps(c, sort_keys=True) != before else "same")
PY
}

# READ-ONLY. set_role is already side-effect-free -- it GETs the config, writes
# a PUT body to a file and says changed/same without sending it -- so a check is
# just calling it for both halves and not following through. Two gateway calls
# at most; a half whose container is down is skipped, which is the ordinary
# state of the backup on a core-only machine and not a failure.
check_pair() {
  local half stanza container verdict diff drift=0
  say "redundancy -- would 'setup' change either half? (reading only)"
  for half in master backup; do
    if [ "$half" = master ]; then stanza="$MASTER_STANZA"; container="$MASTER_CONTAINER"
    else                          stanza="$BACKUP_STANZA"; container="$BACKUP_CONTAINER"; fi
    if ! gateway_running "$container"; then
      dim "  $container is not running -- skipped"
      continue
    fi
    if [ "$half" = master ]; then
      verdict="$(set_role "$stanza" Master "")"
    else
      verdict="$(set_role "$stanza" Backup "$MASTER_GAN_HOST")"
    fi
    diff=''
    if [ -s "$WORK/$stanza.put.json.diff" ]; then
      diff="$(cat "$WORK/$stanza.put.json.diff")"
    fi
    if [ "$verdict" = same ]; then
      ok "$container redundancy settings are what setup would write"
    else
      printf '  drift: %s redundancy %s (Config > Redundancy)\n' \
        "$container" "${diff:-differs from what setup would write}"
      drift=1
    fi
  done
  if [ "$drift" -ne 0 ]; then return 1; fi
  return 0
}

# Point one half at the shared front door. Three things make this fiddly enough
# to be worth a function rather than two curl lines:
#
#  - it is NOT a `resources/` record; web-server config has its own endpoint,
#    and every invented /data/api/v1/... path for it 404s.
#  - `publicHttpPort` / `publicHttpsPort` are ABSENT from the GET while
#    auto-detect is on. Send them anyway; reading-then-merging would drop them.
#  - **the POST can succeed while returning nothing at all.** The web server
#    applies the change and drops the connection mid-response, so the client
#    sees a failure for something that worked. Verified on the backup 06/08/2026.
#    Never read the result from the POST -- re-read the config and check it.
pair() {
  require_gateway "$MASTER_CONTAINER"
  require_gateway "$BACKUP_CONTAINER"

  local changed=0

  say "setting '$MASTER_CONTAINER' to Master"
  if [ "$(set_role "$MASTER_STANZA" Master "")" = changed ]; then
    api "$MASTER_STANZA" PUT /data/api/v1/redundancy/config \
        "$WORK/$MASTER_STANZA.put.json" >/dev/null \
      || die "the master refused its redundancy config"
    ok "master role applied"; changed=1
  else
    ok "master already configured"
  fi

  say "setting '$BACKUP_CONTAINER' to Backup, pointing at $MASTER_GAN_HOST:$MASTER_GAN_PORT"
  if [ "$(set_role "$BACKUP_STANZA" Backup "$MASTER_GAN_HOST")" = changed ]; then
    api "$BACKUP_STANZA" PUT /data/api/v1/redundancy/config \
        "$WORK/$BACKUP_STANZA.put.json" >/dev/null \
      || die "the backup refused its redundancy config"
    ok "backup role applied"; changed=1
  else
    ok "backup already configured"
  fi

  # Certificates, twice, in both directions. The second pass is not paranoia:
  # the backup's certificate does not exist on the master until the backup has
  # tried to connect, and it cannot try until it trusts the master. Approving
  # once approves one direction and leaves the pair looking configured and
  # permanently disconnected.
  local pass
  for pass in 1 2; do
    say "approving Gateway Network certificates (pass $pass of 2)"
    "$REPO_ROOT/scripts/ign-gan.sh" certs "$MASTER_CONTAINER" "$BACKUP_CONTAINER" \
      2>&1 | sed 's/^/  /'
    [ "$pass" = 1 ] && sleep 20
  done

  # A trusted certificate is not an approved connection -- separate act, and
  # until it happens the websocket upgrade answers 403.
  say "approving the incoming redundancy connection on the master"
  node scripts/ign-gw.js gan-approve --gateway "$MASTER_STANZA" 2>&1 | sed 's/^/  /'

  wait_for_pair

  # Last, because it is about how CLIENTS reach the pair rather than how the
  # halves reach each other -- and because the backup restarts itself for module
  # parity during pairing, which would discard a setting made before that.
  say "pointing both halves at the shared front door"
  "$REPO_ROOT/scripts/ign-public-address.sh" "$MASTER_CONTAINER" "$BACKUP_CONTAINER"
}

# Wait for the pair to actually reach a healthy state, and say what it is
# waiting on. The interesting case is module parity: the backup restarts itself
# to install the master's modules, so it stops answering entirely for about a
# minute. Treat "not answering" as progress here, not as failure -- but only for
# as long as its container is still running.
wait_for_pair() {
  # 600, not 300: the backup installs the master's third-party modules on its
  # first sync -- 56MB of them here -- and restarts itself, and the Windows
  # from-scratch bootstrap blew through 300s doing exactly that, failing a
  # step that was working. The pair converged 50s into a re-run.
  local waited=0 timeout=600 s role act proj peer
  say "waiting for the pair to reach Master/Active + Backup/$STANDBY_LEVEL, project Good"
  while [ "$waited" -lt "$timeout" ]; do
    if gateway_running "$BACKUP_CONTAINER" && gateway_running "$MASTER_CONTAINER"; then
      s="$(state_of "$MASTER_STANZA")"
      role="$(echo "$s" | cut -d'|' -f1)"
      act="$(echo "$s"  | cut -d'|' -f2)"
      proj="$(echo "$s" | cut -d'|' -f3)"
      # FIELD 4, and it is the whole point. A master with no peer at all reports
      # exactly Master/Active/Good -- that is what a lone gateway looks like --
      # so checking only the first three fields declares "pair is healthy" for a
      # gateway that is running by itself. `state_of` has returned peerConnected
      # since it was written; this just never read it.
      peer="$(echo "$s" | cut -d'|' -f4)"

      case "$proj" in
        *Modules*) dim "  backup is installing the master's modules and will restart (${waited}s)" ;;
        Good)
          if [ "$role" = Master ] && [ "$act" = Active ] && [ "$peer" = True ]; then
            ok "pair is healthy (${waited}s)"
            echo; show_status
            return 0
          elif [ "$role" = Master ] && [ "$act" = Active ]; then
            dim "  master is up and alone -- the backup has not connected yet (${waited}s)"
          elif [ "$role" = Master ] && [ "$peer" = True ] \
               && [ "$(state_of "$BACKUP_STANZA" | cut -d'|' -f2)" = Active ]; then
            # Handed over to the backup on purpose: paired and healthy, and not
            # this script's to move back.
            ok "pair is healthy, the backup in charge (${waited}s)"
            echo; show_status
            return 0
          fi ;;
        *) dim "  master reports role=$role activity=$act project=$proj (${waited}s)" ;;
      esac
    else
      dim "  a gateway is restarting (${waited}s)"
    fi
    sleep 10; waited=$((waited + 10))
  done

  echo; show_status
  # Say which of the two shapes this is. "Did not reach a healthy state" covers
  # a backup that is mid-sync and a backup that has never made contact, and
  # those have nothing to do with each other -- one needs patience, the other
  # needs the network looked at.
  if [ "$(state_of "$MASTER_STANZA" | cut -d'|' -f4)" != True ]; then
    warn "the master has NO PEER: the backup has never reached it."
    dim  "  Certificates and approvals cannot be the cause yet -- an incoming"
    dim  "  connection only exists once the backup has dialled in. Check that"
    dim  "  first, from the backup itself:"
    dim  "      docker exec $BACKUP_CONTAINER sh -c 'getent hosts $MASTER_GAN_HOST'"
    dim  "      docker logs $BACKUP_CONTAINER --tail 60 | grep -i redundan"
    dim  "  Both halves must be on the 'backbone' network for that name to"
    dim  "  resolve at all, and the master must be listening on $MASTER_GAN_PORT."
  fi
  die "the pair did not reach a healthy state within ${timeout}s -- check: docker logs $BACKUP_CONTAINER"
}

# Which half is currently Active? Failover and resync are asymmetric: the
# request goes to the peer, so it has to be issued from the right side. Sending
# it to a Warm backup is accepted and does nothing visible, which is the kind of
# no-op that reads as a broken button.
active_stanza() {
  local s
  s="$(state_of "$MASTER_STANZA")"
  [ "$(echo "$s" | cut -d'|' -f2)" = Active ] && { printf '%s' "$MASTER_STANZA"; return; }
  printf '%s' "$BACKUP_STANZA"
}

case "$CMD" in
  setup)  pair ;;
  status) show_status ;;
  check)  check_pair ;;

  public-address)
    # Also runs as the last step of `setup`. Separate because it is the one
    # piece of pairing that is about how CLIENTS reach the pair, so it is worth
    # being able to re-apply after a gateway is rebuilt without re-pairing.
    say "pointing both halves at the shared front door"
    "$REPO_ROOT/scripts/ign-public-address.sh" "$MASTER_CONTAINER" "$BACKUP_CONTAINER"
    ;;

  failover)
    from="$(active_stanza)"
    say "asking the ACTIVE half ($from) to hand responsibility to its peer"
    note_request handover
    api "$from" POST /data/api/v1/redundancy/gwaction/failover | sed 's/^/  /'
    dim "give it ~10s, then: scripts/ign-redundancy.sh status"
    ;;

  resync)
    say "forcing a full configuration sync"
    api "$BACKUP_STANZA" POST /data/api/v1/redundancy/gwaction/resync | sed 's/^/  /'
    ;;

  fail)
    # The unplanned half of the demonstration, done from the host: kill the
    # ACTIVE gateway's container outright and start it again after a pause.
    #
    # The page has its own version of this, and it is the one to use in front of
    # a customer -- IgnitionGateway.restart() needs nothing on the host and
    # cannot be left half-done. This exists for the two cases the button cannot
    # cover: a gateway too wedged to run a script, and a demonstration where
    # `docker stop` is exactly the point ("this is the machine losing power",
    # not "this is the software choosing to restart").
    #
    # `docker stop` and not `kill`: the gateway is given its stop_grace_period
    # to close cleanly. A killed gateway recovers too, but it recovers by
    # replaying its journals, which takes longer and muddies the demonstration
    # with recovery messages that are not about redundancy.
    seconds="${2:-60}"
    active="$MASTER_CONTAINER"
    [ "$(state_of "$MASTER_STANZA" | cut -d'|' -f2)" = Active ] || active="$BACKUP_CONTAINER"

    warn "stopping '$active' -- the ACTIVE half -- for ${seconds}s"
    note_request stop
    docker stop "$active" >/dev/null
    ok "$active is down; its peer should take over within ~10s"
    echo
    show_status
    echo
    say "waiting ${seconds}s before bringing it back"
    sleep "$seconds"
    docker start "$active" >/dev/null
    ok "$active is starting again"
    dim "with recovery $RECOVERY_MODE it reclaims its responsibility on return"
    ;;

  unpair)
    # Both halves back to Independent. Wanted rarely, but a paired backup that
    # someone forgot about will keep taking over the master's responsibilities,
    # so leaving no way back would be worse than the ten lines this costs.
    for stanza in "$MASTER_STANZA" "$BACKUP_STANZA"; do
      say "setting $stanza to Independent"
      set_role "$stanza" Independent "" >/dev/null
      api "$stanza" PUT /data/api/v1/redundancy/config "$WORK/$stanza.put.json" >/dev/null \
        && ok "$stanza is Independent"
    done
    ;;

  *) die "usage: ign-redundancy.sh {setup|status|check|public-address|failover|resync|fail [secs]|unpair}" ;;
esac
