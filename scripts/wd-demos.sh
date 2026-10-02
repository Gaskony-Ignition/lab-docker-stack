#!/usr/bin/env bash
#
# The demo console from a terminal.
#
#   scripts/wd-demos.sh                 what exists and what is running
#   scripts/wd-demos.sh start eam       start a demo (and everything it needs)
#   scripts/wd-demos.sh stop eam        stop it, unless another demo needs it
#   scripts/wd-demos.sh stop-all        back to the core alone
#
# It talks to wd-control, which is what the Demos page in GatewayAdmin talks to.
# One implementation of "start a demo", so the page and the command line cannot
# tell you different things about the same machine.
#
# If you want the big hammer, it is still `make down`: that stops the core too,
# which the console deliberately cannot do.
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BASE="$(control_base)"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

api() {  # api <method> <path>
  curl -fsS -X "$1" --max-time 620 "$BASE$2" 2>/dev/null || return 1
}

state() {
  api GET /state || die "wd-control is not answering on $BASE
    It is core, so it should be up:  make up STACK=wd-control"
}

show() {
  state | python3 -c '
import json, sys

def size(n):
    if not n:
        return "?"
    if n >= 1024 ** 3:
        return "%.1fG" % (n / float(1024 ** 3))
    return "%dM" % (n / float(1024 ** 2))

def cost(d):
    """Measured on this machine while the stack ran, not typed into a file --
    so a STOPPED demo still has a real figure.

    `>` means some of its stacks have never run here, so the number is a FLOOR.
    Store & forward measures 29 MB that way -- one Postgres -- for a demo that
    also starts two Ignition gateways."""
    if not d.get("mem"):
        return "?"
    return ("" if not d.get("mem_unknown") else ">") + size(d["mem"])

s = json.load(sys.stdin)
core = s["core"]
print()
print("  CORE     %d/%d up   %s" % (core["up"], core["count"], core["why"]))
print("           " + ", ".join(
    ("%s%s" % (x["stack"], "" if x["up"] else " (DOWN)")) for x in core["stacks"]))
print("           %s up in total, of which %s is core"
      % (cost(s.get("running", {})), cost(core)))
print()
print("  %-14s %-9s %-8s %-6s %s" % ("DEMO", "ASKED", "LIVE", "RAM", "STACKS"))
print("  " + "-" * 78)
for d in s["demos"]:
    print("  %-14s %-9s %-8s %-6s %d/%d  %s" % (
        d["id"], "yes" if d["wanted"] else "-", d["live"], cost(d),
        d["stacks_up"], d["stack_count"],
        ", ".join(x["stack"] for x in d["stacks"])))
    print("  %-14s %s" % ("", d["title"]))
    # WOULD IT WORK, not is it up. `live` is what Docker says; this is the
    # readiness verdict the page gates its tabs on, and the terminal gets the
    # same sentence rather than a second opinion of its own.
    if "ready" in d:
        print("  %-14s %s %s" % (
            "", "ready:" if d["ready"] else "NOT READY:", d.get("readyWhy", "")))
job = s["job"]
if job["state"] not in ("idle",):
    print()
    print("  job: %s -- %s" % (job["state"], job["what"]))
    for line in job["log"][-6:]:
        print("       " + line)
    for line in job.get("warnings") or []:
        print("       WARNING: " + line)
print()
'
}

act() {  # act <path> <what>
  # Not api(): its -f discards the body of a 409, and the body is where the
  # refusal says WHY -- which is the whole point of refusing out loud.
  curl -sS -X POST --max-time 620 "$BASE$1" > "$WORK/reply" 2>/dev/null \
    || die "wd-control is not answering on $BASE"
  if ! python3 -c '
import json, sys
try:
    ok = json.load(open(sys.argv[1])).get("ok")
except ValueError:
    ok = False
sys.exit(0 if ok else 1)' "$WORK/reply"; then
    warn "wd-control refused this, and said why:"
    # `why` is the plain sentence, `error` the older machine-shaped one. Both
    # are sent; prefer the one written for a person.
    python3 -c '
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except ValueError:
    r = {"error": "an answer that was not JSON -- is wd-control healthy?"}
print("  " + (r.get("why") or r.get("error") or "?"))' "$WORK/reply"
    exit 1
  fi

  say "$2"
  # The reconcile runs in wd-control and takes as long as the slowest stack --
  # a gateway is not quick. Waiting is the honest thing: a command that returns
  # while containers are still starting is how you walk into a demonstration
  # with half a stack up.
  seen=0
  while :; do
    sleep 3
    state > "$WORK/state" || die "lost contact with wd-control mid-reconcile"
    seen="$(python3 -c '
import json, sys
job = json.load(open(sys.argv[1]))["job"]
seen = int(sys.argv[2])
for line in job["log"][seen:]:
    print("     " + line, file=sys.stderr)
print(len(job["log"]))
if job["state"] == "failed":
    sys.exit(3)
if job["state"] == "done":
    sys.exit(4)
# A job that carried on past a stack it could not confirm is FINISHED and is
# not "done": the demo has a stack that never came up, and a command that
# printed ok here would be the same lie the card used to tell, with a terminal
# in front of it. (No apostrophes in here -- the whole block is single-quoted.)
if job["state"] == "done_with_warnings":
    for line in job.get("warnings") or []:
        print("     WARNING: " + line, file=sys.stderr)
    sys.exit(5)
' "$WORK/state" "$seen")" && continue
    case $? in
      3) show; die "the reconcile failed -- the log is above" ;;
      4) ok "$2"; break ;;
      5) warn "$2 -- finished, but not everything came up (above)"; show; exit 1 ;;
      *) die "could not read the job state" ;;
    esac
  done
  show
}

case "${1:-list}" in
  list|"")   show ;;
  start)     act "/demos/${2:?usage: wd-demos.sh start <demo>}/start" "starting $2" ;;
  stop)      act "/demos/${2:?usage: wd-demos.sh stop <demo>}/stop" "stopping $2" ;;
  stop-all)  act "/stop-all" "stopping every demo" ;;
  *)         die "unknown command: $1 (try: list, start, stop, stop-all)" ;;
esac
