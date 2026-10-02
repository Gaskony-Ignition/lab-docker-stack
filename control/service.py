#!/usr/bin/env python3
"""The demo console's control plane: what is running, and what you want running.

  GET  /state              every demo, its stacks, and what they are doing now
  POST /demos/<id>/start   add that demo to the desired set, then reconcile
  POST /demos/<id>/stop    remove it, then reconcile
  POST /stop-all           back to the core alone
  POST /mqtt/cut           cut one edge off wd-mqtt for N seconds (token; control/mqttcut.py)
  POST /mqtt/restore       put it back early (token)
  POST /redundancy/request note that a handover or stop was asked for (control/redproof.py)

WHY A RECONCILER RATHER THAN start/stop BUTTONS

Stacks are shared. Both edges carry the EAM push and the store-and-forward road,
so "stop store & forward" must not stop the gateways that the EAM demonstration
is still using. Reference-counting that by hand is the kind of arithmetic that
is right until the day it is not, in front of an audience.

So the page does not start or stop stacks at all. It records which DEMOS you
want, and this reconciles: everything the core needs, plus everything the wanted
demos need, is up; anything else this repo owns is down. Stopping a demo whose
stacks another wanted demo also needs changes nothing, which is the correct
answer and needs no special case to produce.

The desired set is on a volume, so a reboot or a restart of this container does
not lose what you had running.

WHY IT SHELLS OUT TO scripts/stack.sh

Because that is what `make up` runs. One implementation of "bring a stack up",
so the page and the command line cannot drift. It runs in the toolbox image,
which is where every other command in this repo already runs, with the repo
bind-mounted at /work.

THE SOCKET IS THE HOST'S ROOT. This container can start and stop anything on the
machine, and it is unauthenticated on `backbone` -- a deliberate choice for a
demonstration stack on a laptop, and the alternative to putting a credential in
a project resource. It is not published to the LAN by the proxy. Do not copy
this decision to anything a customer runs.

Standard library only, except control/wire.py (THE WIRE's MQTT listener),
which needs paho-mqtt from the toolbox image.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import threading
import urllib.parse
import time
# `escape as esc`, not `import html`: _send_html() builds a local called `html`,
# and a module shadowed by a local in the file that needs it is the next bug.
from html import escape as esc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import autologin
import error_page
import mqttcut
import redproof
import trialkeeper
import wire

REPO = pathlib.Path(os.environ.get("REPO", "/work"))
DEMOS_FILE = REPO / "demos.json"
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
DESIRED_FILE = STATE_DIR / "desired.json"
MEMORY_FILE = STATE_DIR / "memory.json"
PORT = int(os.environ.get("PORT", "8080"))

# How often to sample memory, and how long a stack has to have been up before
# its sample is believed. See sample_memory().
MEMORY_EVERY = 60
MEMORY_SETTLE = 150

# WHICH CONTAINERS ARE OURS
#
# Every service in every stacks/*/compose.yaml carries a label naming its stack:
#
#     labels:
#       au.gaskony.wd.stack: "ignition-edge1"
#
# It is there because the two obvious keys are both wrong.
#
# The COMPOSE PROJECT NAME is not ours to rely on. On this machine it was wrong
# in both directions at once -- `ignition-module-testing` belongs to another
# repo entirely and carries project `ignition`, while our own hub carries
# `wd-ignition`, because stacks/ignition/.env renames it to avoid exactly that
# collision. That .env is gitignored and per machine, so the project name a
# stack ends up with is not even the same on two machines.
#
# The WORKING-DIRECTORY label is closer, but it records the path as the CLIENT
# saw it: a stack started from the host is stamped /home/you/Ignition-Demos-Stack/...
# and the same stack started from in here is stamped /work/..., because that is
# where the repo is mounted. It is kept only as a fallback for containers that
# were started before the label existed.
#
# A label in the committed compose file is none of those things. It says what a
# container is, in git, identically on every machine and from every client.
STACK_LABEL = "au.gaskony.wd.stack"
DIR_LABEL = "com.docker.compose.project.working_dir"

# For the fallback only -- see above.
REPO_HOST = os.environ.get("WD_REPO_HOST", "")
TOOLBOX_ROOT = "/work/stacks"


def demos() -> dict:
    """Read the manifest every time. It is small, and an edited file that needs
    a restart to be noticed is a file people give up editing."""
    return json.loads(DEMOS_FILE.read_text())


def stack_order() -> dict:
    """ORDER from every stack.meta. Order is not decoration: a spoke that starts
    before the hub fetches nothing and caches the failure."""
    out = {}
    for meta in sorted((REPO / "stacks").glob("*/stack.meta")):
        m = re.search(r"^ORDER=(\d+)", meta.read_text(), re.M)
        out[meta.parent.name] = int(m.group(1)) if m else 999
    return out


def ignition_versions(up: dict) -> dict:
    """Which Ignition build each gateway is SET to, and which it is RUNNING.

    Two different questions, and the gap between them is the whole point: a
    chosen version changes nothing until the container is recreated, so a page
    that showed only one number would report a switch as done the moment it was
    picked -- while the gateway you are about to demonstrate from is still on
    the old build.

    READ HERE RATHER THAN SHELLED OUT, unlike readiness. `verify-demos.sh
    --json` is a subprocess because those checks need `docker exec` and take
    tens of seconds; this is a couple of small file reads and rides on the
    `docker ps` we already ran, so it belongs on the poll. `ign-version.sh`
    remains the authority for CHANGING it -- the guards there are the part that
    must not be duplicated.
    """
    chosen = ""
    f = REPO / ".wd-local" / "ignition-version"
    if f.is_file():
        chosen = f.read_text().strip().splitlines()[0].strip() if f.read_text().strip() else ""

    gateways, declared, override, running = [], {}, {}, {}
    for meta in sorted((REPO / "stacks").glob("*/stack.meta")):
        text = meta.read_text().replace("\r", "")
        if not re.search(r"^KIND=gateway$", text, re.M):
            continue
        name = meta.parent.name
        compose = meta.parent / "compose.yaml"
        if not compose.is_file():
            continue
        body = compose.read_text()
        m = re.search(r"image:\s*inductiveautomation/ignition:"
                      r"(?:\$\{IGNITION_VERSION:-)?([0-9][0-9.]*)\}?", body)
        if not m:
            continue        # ignition-ha is a gateway host, not a gateway
        gateways.append(name)
        declared[name] = m.group(1)
        # ONE override for all four, in .wd-local -- the only part of the repo
        # this container can write, and the same file the terminal reads. Four
        # per-stack .env entries could disagree; one file cannot.
        if chosen:
            override[name] = chosen
        for c in up.get(name, []):
            img = c.get("image") or ""
            if "inductiveautomation/ignition:" in img:
                running[name] = img.split(":")[-1]

    def effective(n):
        return override.get(n) or declared.get(n, "")

    eff = {effective(n) for n in gateways if effective(n)}
    run = {running[n] for n in gateways if n in running}
    # PENDING is judged only against gateways that are actually up. A stopped
    # gateway is not "waiting to be restarted", it is stopped -- and calling it
    # pending would leave the page permanently asking for a restart on a rig
    # that is deliberately down, which is its normal resting state.
    pending = sorted(n for n in gateways
                     if n in running and running[n] != effective(n))
    mm = re.search(r"PINNED TO THE IGNITION VERSION[^(]*\(([0-9.]+)\)",
                   (REPO / "scripts" / "modules.manifest").read_text())
    return {
        "gateways": [{"stack": n, "declared": declared.get(n, ""),
                      "override": override.get(n, ""), "effective": effective(n),
                      "running": running.get(n, "")} for n in gateways],
        "effective": sorted(eff)[0] if len(eff) == 1 else "",
        "running": sorted(run)[0] if len(run) == 1 else "",
        # Said out loud rather than inferred from a blank: four gateways on two
        # versions stops a redundant pair syncing and makes an EAM push a
        # gamble, so it is a state the page must name, not one it can omit.
        "agree": len(eff) <= 1,
        "running_agree": len(run) <= 1,
        "pending": pending,
        "module_series": ".".join(mm.group(1).split(".")[:2]) if mm else "",
    }


def stack_ui() -> dict:
    """{stack: {url, open, gateway}} -- where a human clicks to see this stack.

    Derived from TEST_HOST in the manifests, like everything else that names a
    stack, so a new folder gets its button with no edit here.

    `open` is what the button actually points at, and it differs by KIND:
    a gateway goes through the sign-in door, which establishes the session and
    then redirects; anything else is opened as itself. The door is only
    published on gateway hosts (stacks/npm/create-proxy-hosts.sh), so pointing
    a service's button at it would 404.
    """
    out = {}
    for meta in sorted((REPO / "stacks").glob("*/stack.meta")):
        text = meta.read_text().replace("\r", "")

        def field(k, t=text):
            m = re.search(rf"^{k}=(.*)$", t, re.M)
            return m.group(1).strip().strip('"') if m else ""

        host = field("TEST_HOST")
        if not host:
            continue
        # Same rule as autologin.gateway_stacks -- see there for why the
        # pair's front door qualifies without being a gateway itself.
        gateway = field("KIND") == "gateway" or bool(field("SIGNIN_STANZA"))
        url = f"https://{host}"
        out[meta.parent.name] = {
            "url": url,
            "open": f"{url}/_wd/login?next=/web/home" if gateway else url,
            "gateway": gateway,
        }
    return out


def running_stacks() -> dict:
    """{stack: [{name, health, image}]} for everything OF OURS that is up."""
    # `image` rides along on the query we were making anyway. The alternative
    # was `docker inspect` per gateway to answer "which Ignition build is this
    # actually running", which is four more forks on a 3-second poll -- load on
    # the rig purely to describe the rig, which is the trap the readiness panel
    # already exists to avoid.
    fmt = ("{{.Label \"" + STACK_LABEL + "\"}}\t{{.Label \"" + DIR_LABEL
           + "\"}}\t{{.Names}}\t{{.Status}}\t{{.Image}}")
    cp = subprocess.run(["docker", "ps", "--format", fmt],
                        capture_output=True, text=True, timeout=30)
    out: dict[str, list] = {}
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        labelled, workdir, name, status, image = parts
        stack = labelled or _stack_from_path(workdir)
        if not stack:
            continue
        out.setdefault(stack, []).append(
            {"name": name, "health": health(status), "image": image})
    return out


def _stack_from_path(workdir: str) -> str:
    """Fallback for containers started before the label existed.

    Deliberately narrow: only the two roots this repo can produce, so a
    stranger's container in some other .../stacks/<name> directory is not
    adopted on the strength of a directory name.
    """
    if not workdir:
        return ""
    head, _, stack = workdir.rstrip("/").rpartition("/")
    roots = {TOOLBOX_ROOT}
    if REPO_HOST:
        roots.add(f"{REPO_HOST}/stacks")
    return stack if head in roots else ""


def health(status: str) -> str:
    """What `docker ps` says, reduced to the three words a card can show.

    A gateway reports Up within a second and serves nothing for another minute.
    A console that says "running" at that point is lying to whoever is about to
    click through to it, so `starting` is a state in its own right.
    """
    if "(healthy)" in status:
        return "healthy"
    if "health: starting" in status or "(starting" in status:
        return "starting"
    if "(unhealthy)" in status:
        return "unhealthy"
    return "up"


# --- what each demo costs ----------------------------------------------------
#
# The question the page has to answer is "what will this cost me", and the demo
# it asks about is usually STOPPED -- which is exactly when `docker stats` has
# nothing to say. A number typed into demos.json would answer it and then go
# quietly wrong: this stack's own hub went from 4.6 GB to 774 MB in one
# afternoon of tuning, and a hand-written estimate would still be claiming 4.6.
#
# So it is MEASURED WHILE RUNNING AND REMEMBERED. Every stack that has been up
# on this machine has a real figure from this machine, and a stack that has
# never run says so rather than guessing.
#
# Two details that matter more than they look:
#
#   * A sample is only believed after the stack has been up for MEMORY_SETTLE.
#     An Ignition gateway reports about 300 MB thirty seconds in and four times
#     that once it has actually loaded its projects, so an early sample would
#     record a number nothing can be planned with.
#   * The stored figure is REPLACED, not maxed. A high-water mark can never come
#     back down, which would have frozen that 4.6 GB reading for ever.
MEMORY = {}       # {stack: {"bytes": int, "at": epoch}}, persisted
FIRST_UP = {}     # {stack: epoch first seen up}, process-local by design

_UNITS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3,
          "tib": 1024 ** 4, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3}


def parse_bytes(text: str) -> int:
    """`docker stats` prints "1.207GiB / 30.3GiB". Take the first half."""
    m = re.match(r"\s*([0-9.]+)\s*([A-Za-z]+)", text.split("/")[0])
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS.get(m.group(2).lower(), 0))


# HOW LONG EACH STACK TAKES TO BECOME HEALTHY, measured on this machine.
#
# Starting a demo takes one to three minutes and the page used to say nothing
# about how far through it was -- so a start that was going perfectly was
# indistinguishable from one that had wedged, at the exact moment somebody is
# standing in front of a customer deciding whether to reload the page.
#
# Elapsed alone is easy and not enough: "42s" answers "is anything happening"
# but not "should I be worried". The denominator is what makes it useful, and
# the same rule applies as to the RAM figures -- a numerator on its own is how a
# number gets misread. So each stack's ready duration is remembered and the
# estimate for a job is the sum over its plan.
#
# ROLLING, not last-seen. A single cold start after a reboot is minutes slower
# than the steady state, and letting that one reading stand would make every
# later estimate wrong in the direction that invites "it has hung". A 3:1 weight
# toward the existing figure lets a genuinely slower machine converge without
# one outlier dominating.
TIMING = {}       # {stack: seconds}, persisted beside MEMORY
TIMING_WEIGHT = 3


def remember_timing(stack: str, took: int) -> None:
    if took is None or took <= 0:
        return
    old = TIMING.get(stack)
    TIMING[stack] = took if old is None else int(
        (old * TIMING_WEIGHT + took) / float(TIMING_WEIGHT + 1))


def load_memory() -> None:
    try:
        doc = json.loads(MEMORY_FILE.read_text())
        MEMORY.update(doc["stacks"])
        # Timings arrived later than this file did, so an existing file has no
        # such key -- and that is a normal state, not a corrupt one.
        TIMING.update(doc.get("timing") or {})
    except Exception:  # noqa: BLE001 -- a missing or corrupt file just means
        pass           # nothing has been measured yet, which is a real state


def sample_memory() -> None:
    up = running_stacks()
    owner = {c["name"]: stack for stack, cs in up.items() for c in cs}
    if not owner:
        FIRST_UP.clear()
        return
    # Named explicitly rather than sampling everything: `docker stats` with no
    # arguments would walk containers belonging to other repos on this machine,
    # which is both slower and none of our business.
    cp = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"]
        + sorted(owner),
        capture_output=True, text=True, timeout=60)
    totals: dict[str, int] = {}
    for line in cp.stdout.splitlines():
        name, _, usage = line.partition("\t")
        stack = owner.get(name.strip())
        if stack:
            totals[stack] = totals.get(stack, 0) + parse_bytes(usage)

    now = int(time.time())
    for stack, size in totals.items():
        if size <= 0:
            continue
        if now - FIRST_UP.setdefault(stack, now) >= MEMORY_SETTLE:
            MEMORY[stack] = {"bytes": size, "at": now}
    for stack in [s for s in FIRST_UP if s not in totals]:
        del FIRST_UP[stack]

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(
            json.dumps({"stacks": MEMORY, "timing": TIMING},
                       indent=2, sort_keys=True) + "\n")
    except OSError as e:
        print(f"could not write {MEMORY_FILE}: {e}", flush=True)


def memory_loop() -> None:
    while True:
        try:
            sample_memory()
        except Exception as e:  # noqa: BLE001 -- a sampler that dies silently
            print(f"memory sample failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(MEMORY_EVERY)


# --- what you asked for ------------------------------------------------------
def desired() -> list:
    try:
        return json.loads(DESIRED_FILE.read_text())["demos"]
    except Exception:
        return []


def set_desired(ids: list) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DESIRED_FILE.write_text(json.dumps({"demos": ids}, indent=2) + "\n")


# --- the job -----------------------------------------------------------------
# One at a time. Two compose runs against the same shared network and the same
# seeded volumes is a race nobody needs, and a demonstration that starts two
# things at once cannot report either of them honestly.
JOB_LOCK = threading.Lock()
JOB = {"state": "idle", "what": "", "log": [], "started": 0, "finished": 0,
       # WHICH DEMOS THIS JOB IS ACTING ON, so a card can say "this one is
       # starting" rather than only "something is starting". Every demo is
       # BUSY while any job runs -- the lock is one per machine and a click on
       # any other card would be refused -- but only these are the subject.
       "demos": [],
       # Stacks the job carried on past rather than confirmed. A job with any
       # of these finishes `done_with_warnings`: "done" on a demo one stack of
       # which never came up is the lie finding 16 is about.
       "warnings": [],
       # The PLAN, so the page can show progress rather than only elapsed time.
       # "up ignition-edge1" etc, in the order they will be done; `step` is how
       # many are finished. Written before the first one starts, because a
       # denominator that arrives late is a progress bar that jumps.
       # `eta` is the sum of what these stacks have taken here before; the
       # `unmeasured` count is how much of the plan contributed nothing to it,
       # so the page can say "at least" instead of "about". Declared here and
       # not only at job start: an absent key would make the page's shape depend
       # on whether anything had ever run.
       "plan": [], "step": 0, "eta": 0, "unmeasured": 0}
# The most recent click that was turned away, so the page can say so. See
# start_job() for why this cannot live in the reply to the click itself.
#
# `why` is the sentence a button may show verbatim. The page used to have to
# build one from `what` alone, which is why the refusal read as 12px grey
# "Ignored: stopping eam -- one at a time" 700px from the button that caused
# it: the control plane knew both halves -- what you asked for and what was
# already running -- and only sent one.
REFUSED = {"what": "", "at": 0, "why": "", "blocked_by": ""}


def refusal(what: str) -> str:
    """Plain words for a click that arrived while a job was running."""
    return (f"{what} was ignored: {JOB['what'] or 'another job'} is still "
            "running, and this rig does one job at a time.")


def job_snapshot() -> dict:
    return {**JOB, "log": JOB["log"][-12:]}


# How long to let a stack become healthy before starting the next one.
# Generous: an Ignition gateway on a busy laptop takes two to three minutes,
# and giving up early would put us straight back to booting two at once.
#
# AND IT HAS TO SCALE WITH THE MACHINE, because a fixed 300s is a number chosen
# on a 16-core host. An Ignition gateway's start is dominated by classloading
# and the project scan -- both CPU-bound -- so on two cores it takes roughly
# twice as long, and the machine this stack most needs to be patient with is
# exactly the small one. Giving up early does not merely report a failure: the
# reconcile loop moves on and starts the NEXT stack, so the timeout that fired
# because the box was slow is what then puts two JVMs on it at once.
#
# 4+ cores keeps the measured 300s. Two cores gets 600. WD_READY_TIMEOUT
# overrides for a machine that disagrees with the arithmetic.
def _ready_timeout() -> int:
    override = os.environ.get("WD_READY_TIMEOUT")
    if override and override.isdigit():
        return int(override)
    cores = os.cpu_count() or 2
    return min(900, max(300, int(300 * 4 / max(cores, 1))))


READY_TIMEOUT = _ready_timeout()


# --------------------------------------------------------------------------
# Healthy is not serving
# --------------------------------------------------------------------------
#
# Every gateway's container healthcheck is `health-check.sh -s RUNNING`, and a
# gateway waiting on the commissioning wizard answers
# {"state":"RUNNING","details":"COMMISSIONING"} while 302-ing every other
# request to /welcome. So `docker ps` says healthy, the card says Running in
# green, the tab opens, and every button on it is broken.
#
# `lib.sh:wait_for_gateway` has always made this distinction and warned about
# it. Nothing the page could see did, which is finding 5 of the guards audit.
# So the same question is asked here, of the gateway itself.
#
# SAMPLED ON ITS OWN 15s CLOCK, never on the page's 3s poll. One `docker exec
# curl` per running gateway is cheap -- the image's own healthcheck makes the
# identical call every 15 seconds -- but six forks inline would put that cost
# on every binding refresh, which is the trap the readiness panel exists to
# avoid.
PING = {}          # {stack: {"ping": text, "at": epoch}}
PING_EVERY = 15
PING_LOCK = threading.Lock()


def gateway_stacks() -> list:
    """Stacks whose manifest declares KIND=gateway.

    KIND, not "has an Ignition image": `ignition-ha` is the redundant pair's
    HAProxy front door, which serves the gateway UI and answers no StatusPing
    of its own, so probing it would report a permanent "not serving yet" for
    the redundancy demo.
    """
    out = []
    for meta in sorted((REPO / "stacks").glob("*/stack.meta")):
        if re.search(r"^KIND=gateway$", meta.read_text().replace("\r", ""), re.M):
            out.append(meta.parent.name)
    return out


def probe_ping(stack: str) -> str:
    """The gateway's own StatusPing body, or "" if it would not answer.

    The container shares its name with the stack for every gateway in this repo
    (see lib.sh) -- and if it does not exist, "" is the honest answer rather
    than a guess at which sidecar to ask.
    """
    try:
        cp = subprocess.run(
            ["docker", "exec", stack, "curl", "-fsS", "--max-time", "5",
             "http://localhost:8088/StatusPing"],
            capture_output=True, text=True, timeout=15)
        return (cp.stdout or "").strip()
    except Exception:  # noqa: BLE001 -- a gateway that cannot be asked is not
        return ""      # serving, which is what the caller wanted to know


def serving_problem(stack: str, ping: str) -> str:
    """Why this gateway is not serving yet, in plain words. "" when it is."""
    if "COMMISSIONING" in ping:
        # Named, because it does NOT clear itself: somebody has to finish the
        # wizard. Waiting on it is waiting for ever, so both the reconcile and
        # the card say so immediately rather than timing out first.
        return (f"{stack} is stuck in COMMISSIONING -- it answers nothing but "
                "the wizard at /welcome until that is finished")
    if "RUNNING" in ping:
        return ""
    return f"{stack} is up but not serving yet"


def sample_pings() -> None:
    up = running_stacks()
    now = int(time.time())
    live = set()
    for stack in gateway_stacks():
        if not up.get(stack):
            continue
        live.add(stack)
        # THE EXEC HAPPENS OUTSIDE THE LOCK. Held across it, one slow
        # `docker exec` -- and they are slow while a readiness run has the
        # daemon busy -- blocks every /state that wants to know whether a
        # gateway is serving. Measured: /state hung for the length of the
        # sampler's round, which is a page that reads "the control plane is not
        # answering" while the control plane is perfectly well.
        ping = probe_ping(stack)
        with PING_LOCK:
            PING[stack] = {"ping": ping, "at": now}
    with PING_LOCK:
        for stack in [s for s in PING if s not in live]:
            del PING[stack]        # a stopped gateway has no reading, not a
                                   # stale one that would outlive it


def ping_loop() -> None:
    while True:
        try:
            sample_pings()
        except Exception as e:  # noqa: BLE001 -- a sampler that dies silently
            print(f"gateway ping failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(PING_EVERY)


def cached_serving_problem(stack: str) -> str:
    """serving_problem() off the sampler's reading. "" while nothing is known:
    a gateway that has not been asked yet is reported by the container facts
    above it, and claiming a fault on no evidence is the opposite mistake."""
    with PING_LOCK:
        seen = dict(PING.get(stack) or {})
    if not seen:
        return ""
    return serving_problem(stack, seen.get("ping") or "")


def wait_ready(stack: str, timeout: int = READY_TIMEOUT) -> dict:
    """Block until `stack` is ready. {"took": seconds or None, "why": reason}.

    THIS IS WHAT KEEPS THE MACHINE OFF ITS KNEES.

    `docker compose up -d` returns when a container has STARTED, not when it
    works. So the reconcile loop used to fire the next stack about fifteen
    seconds into the previous one -- which for two Ignition gateways means two
    JVMs doing classloading and project scans at the same time. Measured on this
    laptop: 262% and 196% CPU together, load average over 25, and emqx's
    five-second healthcheck timing out five times in a row and marking a
    perfectly good broker unhealthy.

    Nothing was wrong with emqx. The machine simply had nothing left to give it.

    ORDER in stack.meta was already meant to prevent this class of problem, and
    could not: it ordered the LAUNCHES, and a launch is over in a second. This
    orders the READINESS, which is what "start the hub before the spoke" was
    always supposed to mean -- a spoke that connects before its hub is serving
    fetches nothing and caches the failure.

    A container with no healthcheck reports `up` and is treated as ready
    immediately; there is nothing better to wait for.

    AND FOR A GATEWAY, HEALTHY IS NOT ENOUGH. The healthcheck is satisfied by a
    gateway sitting in COMMISSIONING, which serves nothing else -- so the last
    thing this waits for is the gateway's own StatusPing. `why` carries the
    reason a wait gave up, so the job log says which stack and what about it
    rather than only "not healthy yet".
    """
    gateways = set(gateway_stacks())
    started = time.time()
    why = "no container of that stack ever appeared"
    while time.time() - started < timeout:
        containers = stack_containers(stack)
        if containers and all(container_ready(c) for c in containers):
            if stack not in gateways:
                return {"took": int(time.time() - started), "why": ""}
            why = serving_problem(stack, probe_ping(stack))
            if not why:
                return {"took": int(time.time() - started), "why": ""}
            if "COMMISSIONING" in why:
                return {"took": None, "why": why}
        elif containers:
            unready = [c["name"] for c in containers if not container_ready(c)]
            why = f"{', '.join(unready)} did not become healthy"
        time.sleep(5)
    return {"took": None, "why": why}


def stack_containers(stack: str) -> list:
    """Every container the stack owns, running or not.

    `docker ps` without -a cannot see a container in `created` -- and compose
    leaves containers there while it waits on their dependencies. Measured on a
    stack whose main service sat `created` behind a depends_on while the
    sidecars beside it were already `running`; sidecars carry no healthcheck, so
    they report `up`. "Every container I can see is healthy" is then true of a
    stack whose main service has not started, and the gate would wave it
    through.

    -a closes that. It also means a one-shot that FAILED is visible as a
    non-zero exit rather than as an absence, which reads the same as success to
    anything counting only what is running.
    """
    cp = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={STACK_LABEL}={stack}",
         "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
        capture_output=True, text=True, timeout=30)
    out = []
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out.append({"name": parts[0], "state": parts[1], "status": parts[2]})
    return out


def container_ready(c: dict) -> bool:
    """Ready, still coming up, or wrong -- for one container."""
    if c["state"] == "exited":
        # A one-shot that did its job: the render and bundle containers exist to
        # run once and stop, and compose already gates on them completing.
        # A non-zero exit is NOT ready, and waiting until the timeout is the
        # right answer -- it puts the failure in the job log.
        return "Exited (0)" in c["status"]
    if c["state"] != "running":
        return False        # created, restarting, paused, dead
    return health(c["status"]) in ("healthy", "up")


def run_reconcile(what: str, subjects: list | None = None) -> None:
    d = demos()
    order = stack_order()
    core = list(d["core"]["stacks"])
    wanted = set(core)
    by_id = {x["id"]: x for x in d["demos"]}
    for demo_id in desired():
        if demo_id in by_id:
            wanted.update(by_id[demo_id]["stacks"])

    ours = set(order)
    up = set(running_stacks()) & ours
    to_start = sorted(wanted - up, key=lambda s: order.get(s, 999))
    # Reverse order on the way down, so spokes let go of the hub first. The core
    # is always in `wanted`, so it is never a candidate here -- that is the only
    # guard the page needs, and it is structural rather than a check somebody
    # could forget to write.
    to_stop = sorted((up - wanted) & ours, key=lambda s: -order.get(s, 999))

    plan = [f"down {s}" for s in to_stop] + [f"up {s}" for s in to_start]
    if to_start:
        plan.append("settings")
    # The estimate is the sum of what each stack HAS taken here before. Bringing
    # one down is quick and unmeasured, so it contributes a flat few seconds --
    # and a stack never started on this machine contributes nothing, which makes
    # the estimate a floor rather than a guess. The page is told how much of the
    # plan is unmeasured so it can say "about" or say nothing.
    eta = sum(TIMING.get(s, 0) for s in to_start) + 3 * len(to_stop)
    unmeasured = len([s for s in to_start if s not in TIMING])

    JOB.update(state="working", what=what, log=[], started=int(time.time()),
               finished=0, plan=plan, step=0, eta=eta, unmeasured=unmeasured,
               demos=list(subjects or []), warnings=[])
    try:
        for verb, stacks in (("down", to_stop), ("up", to_start)):
            for s in stacks:
                JOB["log"].append(f"{verb} {s}")
                cp = subprocess.run(
                    ["bash", str(REPO / "scripts" / "stack.sh"), verb, s],
                    capture_output=True, text=True, timeout=600, cwd=str(REPO))
                if cp.returncode != 0:
                    tail = (cp.stderr or cp.stdout).strip().splitlines()[-3:]
                    JOB["log"].append(f"FAILED {verb} {s}: {' / '.join(tail)}")
                    JOB.update(state="failed", finished=int(time.time()))
                    return
                if verb == "up":
                    got = wait_ready(s)
                    took = got["took"]
                    if took is not None:
                        JOB["log"].append(f"{s} ready ({took}s)")
                    else:
                        # CARRYING ON IS RIGHT; CALLING IT DONE IS NOT. The
                        # next stack still gets its turn -- a wedged edge must
                        # not block the rest of a demonstration -- but the job
                        # remembers, ends `done_with_warnings`, and the demo
                        # does not read ready.
                        JOB["log"].append(f"{s}: {got['why']} -- carrying on")
                        JOB["warnings"].append(f"{s}: {got['why']}")
                    # Only a stack that actually became healthy teaches us
                    # anything. A timeout records nothing rather than recording
                    # READY_TIMEOUT, which would poison every later estimate
                    # with the one run that went wrong.
                    remember_timing(s, took)
                JOB["step"] = JOB["step"] + 1
        if to_start:
            # WHAT BOOTSTRAP SETS, ON THE GATEWAYS JUST STARTED. A machine built
            # before a setting existed otherwise never gets it (scripts/converge.sh).
            # --starting: these gateways have only just come up, so the steps
            # that bounce a transmitter or historian interrupt nothing.
            JOB["log"].append("applying gateway settings")
            # To a file, not captured: a run killed by the timeout used to take
            # its output with it, and the job said only "timed out" (30/09/2026,
            # a redundancy start that never said which step hung).
            out_path = REPO / ".wd-local" / "converge-last.log"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as out:
                try:
                    rc = subprocess.run(
                        ["bash", str(REPO / "scripts" / "converge.sh"), "--starting", "--if-needed", *to_start],
                        stdout=out, stderr=subprocess.STDOUT, text=True, timeout=1200,
                        cwd=str(REPO)).returncode
                except subprocess.TimeoutExpired:
                    rc = None
            try:
                os.chmod(out_path, 0o666)
            except OSError:
                pass
            if rc == 0:
                JOB["log"].append("gateway settings in place")
            else:
                text = out_path.read_text(errors="replace")
                why = [l.strip()[2:] for l in text.splitlines() if l.strip().startswith("- ")]
                if rc is None:
                    why.append("the settings run passed 20 minutes and was stopped")
                JOB["log"].append("some gateway settings did not apply")
                JOB["warnings"].append("settings: " + ("; ".join(why) or "see the log")
                                       + " -- .wd-local/converge-last.log has the detail")
            JOB["step"] = JOB["step"] + 1
        if not to_start and not to_stop:
            JOB["log"].append("nothing to do -- already in that shape")
        JOB.update(state="done_with_warnings" if JOB["warnings"] else "done",
                   finished=int(time.time()))
    except Exception as e:  # noqa: BLE001 -- a stuck job must still report
        JOB["log"].append(f"{type(e).__name__}: {e}")
        JOB.update(state="failed", finished=int(time.time()))
    finally:
        JOB_LOCK.release()
        # READINESS RUNS BECAUSE SOMETHING STARTED, not because somebody
        # clicked. Until this line, `verify.at` on this machine was 0 -- the
        # one panel that would have caught a half-dead demo was opt-in, and
        # nobody opts in while walking into a meeting. Queued, not run here:
        # the lock is already released, so a click arriving now is accepted
        # rather than refused by a 40-second readiness check.
        queue_ready(desired())



# --------------------------------------------------------------------------
# Which Ignition builds you may pick
# --------------------------------------------------------------------------
#
# FETCHED IN THE BACKGROUND, NEVER ON THE POLL. Docker Hub is a network call
# measured in hundreds of milliseconds, and /state answers a 3-second timer on
# two views. So the tag list is refreshed on its own clock and the poll always
# serves whatever is cached -- an empty list on the first few polls after a
# restart, which the page renders as "checking" rather than as "no versions".
TAGS = {"at": 0, "list": [], "error": ""}
TAGS_TTL = 6 * 3600
TAGS_LOCK = threading.Lock()


def _vt(v: str):
    """A version as a tuple of ints, for comparing. Anything unparseable sorts
    lowest rather than raising -- a stray tag on Docker Hub must not be able to
    break the picker."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (-1,)


def refresh_tags() -> None:
    import urllib.request
    url = ("https://hub.docker.com/v2/repositories/inductiveautomation/"
           "ignition/tags?page_size=100")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            doc = json.loads(r.read().decode())
        names = [x.get("name", "") for x in doc.get("results", [])]
        # Only plain version tags. Hub also carries `latest`, `8.3`, nightly and
        # edition-suffixed names, and none of those is a thing to pin a
        # demonstration rig to -- `latest` in particular would silently change
        # what the rig runs the next time it was pulled.
        good = sorted({n for n in names if re.fullmatch(r"[0-9]+(\.[0-9]+)+", n)},
                      key=_vt, reverse=True)
        with TAGS_LOCK:
            TAGS.update(at=int(time.time()), list=good, error="")
    except Exception as e:
        with TAGS_LOCK:
            TAGS.update(at=int(time.time()), error=f"{type(e).__name__}: {e}")


def tags_loop() -> None:
    while True:
        with TAGS_LOCK:
            due = time.time() - TAGS["at"] > TAGS_TTL
        if due:
            refresh_tags()
        time.sleep(60)


def selectable(v: dict) -> list:
    """The versions the picker may OFFER -- which is not every tag that exists.

    A picker that can offer something the script will refuse is a picker that
    teaches people the buttons lie, so the same three constraints ign-version.sh
    enforces are applied here as a filter:

      * the module series, because a module built for another minor is skipped
        SILENTLY -- the Architecture tab and Site 2's Sparkplug road simply are
        not there and everything else looks perfect;
      * not older than any build these volumes have run, because a gateway
        upgrades its config store on first start and cannot read it back;
      * the floor, which the series filter already implies.

    The script still checks all of it. This is the half that keeps the offer
    honest, not the half that keeps it safe.
    """
    with TAGS_LOCK:
        tags = list(TAGS["list"])
    series = v.get("module_series") or ""
    floor = max([_vt(x) for x in
                 [v.get("running") or "", v.get("effective") or ""] if x]
                or [(-1,)])
    out = [t for t in tags
           if (not series or ".".join(t.split(".")[:2]) == series)
           and _vt(t) >= floor]
    # The current one always appears, even if Hub has stopped listing it: a
    # picker whose list does not contain what you are running reads as broken.
    cur = v.get("effective") or ""
    if cur and cur not in out:
        out.append(cur)
    return sorted(set(out), key=_vt, reverse=True)


def _ignition_block(up: dict) -> dict:
    v = ignition_versions(up)
    # NOT inside `with TAGS_LOCK`. selectable() takes that lock itself, and
    # threading.Lock is NOT reentrant -- nesting them deadlocked /state on the
    # first request, which presents as a page stuck on "the control plane is not
    # answering" while /healthz keeps returning 200 because it touches neither.
    v["available"] = selectable(v)
    # WHEN THIS RIG WAS LAST BACKED UP. Shown, not enforced: taking a .gwbk
    # writes to the repo, which this container mounts read-only on purpose, so
    # gating on something the page cannot itself perform would only send you to
    # a terminal you were trying to avoid. The number is the honest half --
    # "no backup on this machine" beside a forward-only switch is the sentence
    # that makes somebody stop and think.
    newest = 0
    gwbk = REPO / ".gwbk"
    if gwbk.is_dir():
        for f in gwbk.glob("*.gwbk"):
            try:
                newest = max(newest, int(f.stat().st_mtime))
            except OSError:
                pass
    v["backup_at"] = newest
    with TAGS_LOCK:
        # Said out loud rather than left as an empty list. "Docker Hub is
        # unreachable" and "there is nothing newer" are different answers and
        # the picker must not render them the same way.
        v["available_checked"] = bool(TAGS["at"])
        v["available_error"] = TAGS["error"]
    return v


def start_version_job(want: str) -> tuple:
    """Choose an Ignition build and recreate the gateways that are UP.

    ONLY THE ONES THAT ARE UP, and that is the design rather than a shortcut.
    The version is recorded for all four; a gateway that is stopped -- which on
    this rig is most of them, most of the time -- picks it up the next time it
    starts. Bringing the edges and the backup up to apply a version change would
    start demonstrations nobody asked for and cost several GB, to change a
    number on a gateway that is not running.

    THE HUB IS ONE OF THEM, so this tears down the web server that served the
    click. That is survivable here for one reason: the job runs in THIS
    container, not in the gateway, so the log outlives the gateway going away
    and the session reads the outcome when it reconnects. It is the same trap
    the redundancy button avoids by scheduling its restart -- there the script
    doing the restarting was the process writing the response, and the click
    reported failure for something that worked.
    """
    if not JOB_LOCK.acquire(blocking=False):
        # THE CLICK THAT WAS REFUSED, not the job that refused it. This used to
        # record `what=JOB["what"]`, so a version switch turned away during a
        # start reported "Ignored: starting eam" -- the page named the thing
        # that was working as the thing it had thrown away. start_job() had it
        # right; the two call sites now say the same thing.
        what = f"switching to Ignition {want}"
        REFUSED.update(at=int(time.time()), what=what, why=refusal(what),
                       blocked_by=JOB["what"])
        return 409, {"ok": False, "error": f"busy: {JOB['what']}",
                     "why": refusal(what), "what": what,
                     "job": job_snapshot()}
    threading.Thread(target=_version_job, args=(want,), daemon=True).start()
    return 202, {"ok": True, "job": job_snapshot()}


def _version_job(want: str) -> None:
    order = stack_order()
    try:
        JOB.update(state="working", what=f"switching to Ignition {want}",
                   log=[], started=int(time.time()), finished=0,
                   plan=[f"set {want}"], step=0, eta=0, unmeasured=1,
                   # No demo is the subject: this recreates whichever gateways
                   # are up, which belong to several. Every card is busy all
                   # the same -- one job at a time is a property of the rig.
                   demos=[], warnings=[])

        # THE GUARDS LIVE IN THE SCRIPT, not here. Duplicating the floor, the
        # module check and the downgrade refusal in Python would mean two
        # answers to the same question, and the one that ran from the page
        # would be the one nobody tested.
        cp = subprocess.run(
            ["bash", str(REPO / "scripts" / "ign-version.sh"), want],
            capture_output=True, text=True, timeout=120, cwd=str(REPO))
        if cp.returncode != 0:
            msg = (cp.stderr or cp.stdout).strip().splitlines()
            JOB["log"].append("refused: " + " / ".join(msg[:4]))
            JOB.update(state="failed", finished=int(time.time()))
            return
        JOB["log"].append(f"set to {want}")

        up = running_stacks()
        gws = [g["stack"] for g in ignition_versions(up)["gateways"]
               if g["stack"] in up]
        if not gws:
            JOB["log"].append("no gateway is running -- it applies on the next up")
            JOB.update(state="done", finished=int(time.time()))
            return
        JOB["plan"] = [f"recreate {g}" for g in gws]
        JOB["eta"] = sum(TIMING.get(g, 60) for g in gws)

        # Down in reverse start order so the spokes let go of the hub first,
        # up in start order for the same reason -- the same rule reconcile
        # follows, and the reason it is not simply "restart each in turn".
        for g in sorted(gws, key=lambda s: -order.get(s, 999)):
            JOB["log"].append(f"down {g}")
            if not _stack(g, "down"):
                return
        for g in sorted(gws, key=lambda s: order.get(s, 999)):
            JOB["log"].append(f"up {g}")
            if not _stack(g, "up"):
                return
            got = wait_ready(g)
            if got["took"]:
                JOB["log"].append(f"{g} ready in {got['took']}s")
            else:
                JOB["log"].append(f"{g}: {got['why']}")
                JOB["warnings"].append(f"{g}: {got['why']}")
            JOB["step"] = JOB["step"] + 1
        JOB.update(state="done_with_warnings" if JOB["warnings"] else "done",
                   finished=int(time.time()))
    except Exception as e:
        JOB["log"].append(f"{type(e).__name__}: {e}")
        JOB.update(state="failed", finished=int(time.time()))
    finally:
        JOB_LOCK.release()
        # Recreating a gateway retires every readiness verdict that mentioned
        # it -- on a new build, nothing that was checked was checked here.
        queue_ready(desired())


def _stack(name: str, verb: str) -> bool:
    cp = subprocess.run(["bash", str(REPO / "scripts" / "stack.sh"), verb, name],
                        capture_output=True, text=True, timeout=600, cwd=str(REPO))
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout).strip().splitlines()[-3:]
        JOB["log"].append(f"FAILED {verb} {name}: {' / '.join(tail)}")
        JOB.update(state="failed", finished=int(time.time()))
        return False
    return True


def start_job(what: str, mutate, subjects: list | None = None) -> tuple:
    """Take the lock FIRST, then record what you want, then reconcile.

    These used to be the other way round: the handler wrote the desired set and
    then asked for the lock, so a click refused as "busy" had already changed
    what the machine wants. Nothing reconciles when a job ENDS, so that desire
    was never acted on -- the machine sat running stacks nobody had asked for,
    and the console would not clean them up until you clicked something else.
    Found by a stop-all that arrived while a start was still running: the job
    log said "starting store-forward" while desired.json said [].

    "Busy" now means what it says. The refused click changes nothing at all.
    """
    if not JOB_LOCK.acquire(blocking=False):
        # Record it HERE, and let the page's poll render it.
        #
        # The 409 is also returned to the caller, and the button writes it to a
        # session property -- but two buttons write the same property and the
        # calls do not come back in the order they were made. A refusal is
        # instant while the accepted click before it is still in flight, so the
        # refusal was written and then overwritten by the older click's
        # "starting store-forward". The page ended up saying nothing at all
        # about the click it had just thrown away.
        #
        # Sequencing the writes in the button script does not fix it either:
        # Perspective queues session-property writes, so a script cannot read
        # back what it just wrote and check whether it is still the newest.
        #
        # The refusal happened here, so it is recorded here, and it reaches the
        # page through the same three-second poll as everything else. Nothing
        # races because nothing else writes it.
        REFUSED.update(what=what, at=int(time.time()), why=refusal(what),
                       blocked_by=JOB["what"])
        return 409, {"ok": False, "error": f"busy: {JOB['what']}",
                     # The sentence a button may show as it stands. Sent in the
                     # reply as well as recorded above, because a caller that is
                     # not the page -- wd-demos.sh -- has only the reply.
                     "why": refusal(what), "what": what,
                     "job": job_snapshot()}
    try:
        mutate()
    except Exception as e:  # noqa: BLE001 -- never strand the lock
        JOB_LOCK.release()
        return 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}
    # run_reconcile releases the lock in its finally.
    threading.Thread(target=run_reconcile, args=(what, subjects),
                     daemon=True).start()
    return 200, {"ok": True, "job": job_snapshot()}


# --- what the page renders ---------------------------------------------------
def _on_docker_desktop() -> bool:
    """Is this daemon Docker Desktop, i.e. is /proc/meminfo a VM's and not a
    host's?

    Asked of the DAEMON rather than guessed from the container. `docker info`
    is authoritative and this process already holds the socket; sniffing
    /proc/version for "microsoft" would also match a plain WSL2 install where
    the memory figure is the host's after all.

    Cached: the answer cannot change while this process lives, and state() is
    polled every few seconds.
    """
    global _DOCKER_DESKTOP
    if _DOCKER_DESKTOP is None:
        try:
            cp = subprocess.run(["docker", "info", "--format", "{{.OperatingSystem}}"],
                                capture_output=True, text=True, timeout=10)
            _DOCKER_DESKTOP = "docker desktop" in (cp.stdout or "").strip().lower()
        except Exception:
            _DOCKER_DESKTOP = False    # unknown -> claim nothing extra
    return _DOCKER_DESKTOP


_DOCKER_DESKTOP = None


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------
#
# `scripts/verify-demos.sh` already knows every readiness check and names a
# repair for each. This runs THAT, rather than reimplementing any of it in
# Jython -- the checks need `docker exec` and the gateway's REST API from the
# host, and a Perspective script has neither. One implementation of "is this
# demonstration ready", used by the terminal and the page alike, is the same
# rule `stack.sh` follows for "bring a stack up".
#
# CACHED AND EXPLICIT, never on the page's 3s poll. A full run takes tens of
# seconds and shells into every gateway; running it on a timer would put a load
# on the rig whose only purpose is to describe the load on the rig. So the page
# shows the last result with its age and a Re-check button, which is honest
# about being a snapshot instead of pretending to be live.
VERIFY = {"at": 0, "running": False, "result": None}
VERIFY_LOCK = threading.Lock()

# THE SAME RUN, READ PER DEMO.
#
# `ready` on a card cannot be "its containers are healthy" -- that is the whole
# finding. It is the verdict of the readiness run, attributed by the `SECTION`
# the run already stamps on every check, and it is kept per demo with the SHAPE
# of the machine it was taken on. A verdict is only ever about that shape: a
# stack going down, or a gateway leaving COMMISSIONING, retires it rather than
# leaving a green tick over a demo that has changed underneath it.
#
# HOW IT STAYS CHEAP ENOUGH FOR A 3-SECOND POLL: /state never runs anything. It
# reads this dict. The run happens
#
#   * at the end of every reconcile, for the demos that were asked for;
#   * at start-up, for the demos that were already wanted;
#   * lazily, when a poll finds a demo whose containers are all serving and
#     whose verdict is missing, retired by a shape change, or older than
#     READY_TTL -- queued, coalesced into one run, and never while a job runs.
#
# Measured on this machine: `--json --quick DEMO=eam` takes 61s with the edges
# down and `DEMO=sparkplug` 42s with all four stacks up. That is why the batch
# is ONE run for every queued demo rather than one run each, why there is no
# clock behind it, and why a stale verdict is left standing while its
# replacement is fetched -- flipping a tab to "checking" ten minutes into a
# demonstration would be a worse lie than an age on screen.
READY = {}          # {demo: {"ok", "why", "at", "shape", "checked"}}
READY_TTL = 600
READY_LOCK = threading.Lock()
READY_QUEUE = set()
READY_INFLIGHT = set()
READY_WAKE = threading.Event()


def verify_snapshot() -> dict:
    with VERIFY_LOCK:
        return {"at": VERIFY["at"], "running": VERIFY["running"],
                "result": VERIFY["result"]}


def demo_shape(demo_id: str, up: dict, by_id: dict, gws=None) -> str:
    """A fingerprint of what this demo's stacks are doing, for the verdict to
    be pinned to. Cheap: every argument is already in hand on the poll."""
    parts = []
    gws = set(gateway_stacks()) if gws is None else gws
    for s in (by_id.get(demo_id) or {}).get("stacks", []):
        states = sorted({c["health"] for c in up.get(s) or []}) or ["down"]
        part = f"{s}:{'+'.join(states)}"
        if s in gws and up.get(s):
            # Serving or commissioning is part of the shape, not a detail of
            # it. Without this a gateway that finished its wizard would keep
            # the failing verdict taken while it was stuck, for ten minutes.
            part += "/" + ("commissioning" if cached_serving_problem(s) else "ok")
        parts.append(part)
    return ",".join(parts)


def queue_ready(demo_ids) -> None:
    """Ask for a readiness run on these demos. Never blocks, never duplicates.

    A demo already IN a run is not queued again. Without that, every poll
    during the forty seconds a run takes queues the same demo -- the verdict
    does not exist yet, which is precisely why the run is happening -- and the
    worker starts a second identical run the moment the first one lands.
    Measured: two full runs per start, back to back.
    """
    with READY_LOCK:
        ids = {d for d in (demo_ids or []) if d and d not in READY_INFLIGHT}
        if not ids:
            return
        READY_QUEUE.update(ids)
    READY_WAKE.set()


def ready_loop() -> None:
    while True:
        READY_WAKE.wait(60)
        READY_WAKE.clear()
        with READY_LOCK:
            batch = sorted(READY_QUEUE)
            READY_QUEUE.clear()
        # ONLY DEMOS THAT ARE UP. A stopped demo is answered by the container
        # facts alone -- and passing it as DEMO= makes its stacks "wanted" in
        # the run, which files "ignition-backup is not running" under the
        # section every other demo is judged on. A reconcile that STOPPED
        # redundancy used to queue it here for exactly that reason.
        up = running_stacks()
        by_id = {x["id"]: x for x in demos()["demos"]}
        batch = [i for i in batch
                 if all(up.get(s) for s in (by_id.get(i) or {}).get("stacks", []))]
        if not batch:
            continue
        if JOB["state"] == "working":
            # Asking a machine that is still changing shape produces a verdict
            # about neither shape, and the load would land on the start it is
            # describing. Put it back and wait the job out.
            time.sleep(10)
            queue_ready(batch)
            continue
        try:
            # Every queued demo that is up, in one run. The run is also what
            # the readiness PANEL shows; its `wanted` names the scope.
            run_verify(batch)
        except Exception as e:  # noqa: BLE001 -- a worker that dies silently
            print(f"readiness run failed: {type(e).__name__}: {e}", flush=True)


def stacks_named(text: str) -> set:
    """Which of this repo's stacks a sentence mentions, by whole name --
    `ignition` must not match inside `ignition-edge1`."""
    names = [m.parent.name for m in (REPO / "stacks").glob("*/stack.meta")]
    return {n for n in names
            if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)}


def absorb_verify(doc: dict, demo_ids: list, shapes: dict) -> None:
    """Turn one readiness document into a per-demo verdict.

    CORE COUNTS AGAINST EVERY DEMO. The run files each check under a section,
    and the container and trial checks for a demo's own stacks are filed under
    `core` -- correctly, since a lapsed trial or a stopped edge is not a fact
    about one demonstration. So a demo is ready when its own section and the
    core section both pass, which is also the honest answer: a broken core is
    not a demonstration you can give.
    """
    at = int(time.time())
    sections = {s.get("demo"): s for s in (doc.get("sections") or [])}
    core = sections.get("core") or {}
    d = demos()
    true_core = set(d["core"]["stacks"])
    owned = {x["id"]: set(x["stacks"]) for x in d["demos"]}
    for demo_id in demo_ids:
        mine = sections.get(demo_id) or {}
        if doc.get("error"):
            ok, why, checked = False, str(doc["error"]), 0
        else:
            ours = true_core | owned.get(demo_id, set())
            # `core` in the document is not the core in demos.json: it also
            # holds the container and trial checks for EVERY demo in the run.
            # So a core problem naming another demo's stack is that demo's
            # problem, and letting it through is how Sparkplug came to read
            # "ignition-edge1 is not running".
            shared = [pr for pr in (core.get("problems") or [])
                      if not (stacks_named(pr.get("what", ""))
                              - ours)]
            problems = shared + list(mine.get("problems") or [])
            checked = int(core.get("checked") or 0) + int(mine.get("checked") or 0)
            ok = not problems
            if ok:
                # The count is the scope. "ready" on its own is a claim about
                # everything; "ready -- 15 checks passed" is a claim about what
                # was actually asked, which is all any check can offer.
                why = f"ready -- {checked} checks passed"
            else:
                why = problems[0]["what"]
                if len(problems) > 1:
                    why += f" (and {len(problems) - 1} more)"
        with READY_LOCK:
            READY[demo_id] = {"ok": ok, "why": why, "at": at,
                              "shape": shapes.get(demo_id, ""),
                              "checked": checked}


def run_verify(demo_ids: list | None = None) -> None:
    """Run verify-demos.sh --json --quick and cache the document.

    --quick because this is the interactive path: the slow checks are the ones
    that sample history rows over several seconds, and a page with a spinner on
    it for a minute is a page people stop pressing. The terminal keeps the full
    run.

    `demo_ids` becomes DEMO=<ids> -- the script's own way of being asked about
    named demos. With none, the script asks this control plane what was wanted,
    which is what the Re-check button has always done.

    A non-zero exit is EXPECTED -- it means checks failed, which is exactly the
    case the panel exists for -- so the return code is not an error here; only a
    document that will not parse is.
    """
    with VERIFY_LOCK:
        if VERIFY["running"]:
            return
        VERIFY["running"] = True
    asked, shapes, doc = [], {}, None
    try:
        # Taken BEFORE the run, not after: the shape a verdict belongs to is
        # the one the checks were made against, and the run takes the better
        # part of a minute. Inside the try, so a manifest that will not read
        # cannot leave VERIFY["running"] stuck true and readiness frozen.
        up = running_stacks()
        by_id = {x["id"]: x for x in demos()["demos"]}
        asked = list(demo_ids or desired())
        shapes = {d: demo_shape(d, up, by_id) for d in asked}
        with READY_LOCK:
            READY_INFLIGHT.update(asked)
        env = dict(os.environ)
        if demo_ids:
            env["DEMO"] = ",".join(demo_ids)
        proc = subprocess.run(
            ["bash", str(REPO / "scripts" / "verify-demos.sh"), "--json", "--quick"],
            cwd=str(REPO), capture_output=True, text=True, timeout=300, env=env)
        try:
            doc = json.loads(proc.stdout)
        except ValueError:
            # stderr, not stdout: the human stream is where the reason will be.
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            doc = {"error": "verify-demos did not return a document",
                   "detail": " / ".join(tail)}
        with VERIFY_LOCK:
            VERIFY["result"] = doc
            VERIFY["at"] = int(time.time())
    except subprocess.TimeoutExpired:
        doc = {"error": "verify-demos timed out after 300s"}
        with VERIFY_LOCK:
            VERIFY["result"] = doc
            VERIFY["at"] = int(time.time())
    except Exception as e:  # noqa: BLE001
        doc = {"error": f"{type(e).__name__}: {e}"}
        with VERIFY_LOCK:
            VERIFY["result"] = doc
            VERIFY["at"] = int(time.time())
    finally:
        with VERIFY_LOCK:
            VERIFY["running"] = False
        # Recorded even when the run failed, so a rig where the check itself is
        # broken is not asked again every three seconds.
        absorb_verify(doc or {"error": "the readiness run did not finish"},
                      asked, shapes)
        with READY_LOCK:
            READY_INFLIGHT.difference_update(asked)


def host_load() -> dict:
    """One-minute load average, per core. The CPU number worth reporting.

    RAM is what decides whether a demo can be STARTED; CPU is what explains why
    the machine is unresponsive right now, and the two need different treatment.

    NOT a CPU percentage. The measured incident this stack actually suffers is
    two Ignition JVMs classloading and scanning at once -- 262% and 196%
    together, load average over 25, and emqx's five-second healthcheck timing
    out five times in a row so a perfectly good broker was marked unhealthy
    (see wait_ready). During that minute high CPU is CORRECT, so a percentage on
    screen would be flashing red at exactly the moment nothing is wrong, and
    people would learn to ignore it. Load average moves slowly enough to mean
    something, and it is the number that was over 25.

    PER CORE, because raw load is meaningless without the denominator -- the
    same mistake the RAM figure made before it grew one. 4.0 on eight cores is a
    busy machine working fine; on two cores it is a machine in trouble.

    /proc/loadavg, like /proc/meminfo above, is not namespaced, so this is the
    host's figure on Linux and the Docker VM's under Docker Desktop -- which is
    the right one either way, being where the containers actually run.
    """
    try:
        with open("/proc/loadavg") as fh:
            one = float(fh.read().split()[0])
        cores = os.cpu_count() or 1
        return {"load": round(one, 2), "cores": cores,
                "load_per_core": round(one / cores, 2)}
    except Exception:
        # Same rule as the memory read: a missing figure must not take the page
        # down. It just means the page says nothing about CPU.
        return {}


def host_memory() -> dict:
    """How much RAM the machine has, and how much is left.

    Per-stack figures are useless without this. "~2.3 GB" means one thing on a
    32 GB workstation and something else entirely on the 8 GB laptop somebody
    runs a demonstration from -- and the page was showing the numerator with no
    denominator, which is how a stack gets started that cannot fit.

    /proc/meminfo INSIDE this container is the right source, not a mistake. The
    kernel does not namespace it, so a Linux host reports its own RAM; under
    Docker Desktop it reports the WSL2/HyperKit VM's allocation, which is
    precisely the budget the containers are competing for. Reading the Windows
    host's physical total there would be the wrong number -- Docker cannot use
    it.

    MemAvailable, not MemFree: free memory excludes reclaimable page cache and
    on a warm machine reads near zero, which would make every demo look
    impossible. MemAvailable is the kernel's own estimate of what a new
    workload can actually have.

    BUT SAY WHOSE MEMORY IT IS. On Docker Desktop the number is Docker's
    allowance, and calling it "the machine" is a lie with consequences: measured
    on the Windows host 22/08/2026, the page read 23.7 GB free while WINDOWS had
    4.4 GB free. Docker's VM takes its memory FROM the host as it grows, and
    host starvation is the exact failure that killed the Linux VM beside it --
    so the one reading that looks reassuring is the one that cannot see the
    danger. `docker_desktop` is reported so the page can name the number
    honestly rather than pretend it is the host's.
    """
    try:
        vals = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    vals[key] = int(parts[0]) * 1024      # kB -> bytes
        total = vals.get("MemTotal")
        avail = vals.get("MemAvailable")
        if not total:
            return {}
        out = {"total": total, "docker_desktop": _on_docker_desktop()}
        if avail is not None:
            out["available"] = avail
            out["used"] = total - avail
        out.update(host_load())
        return out
    except Exception:
        # A missing denominator must not take the page down -- it just means
        # the cards fall back to bare per-demo figures, which is what they
        # showed before this existed.
        return {}


# --- which release this machine is on ----------------------------------------
#
# The console has to be able to say, in its own header, which release it is --
# and whether anything here has been changed since, because an update will
# overwrite that. The gateway cannot answer either question: it cannot run git,
# and it has no idea what a setup script would have written.
#
# SO THIS BLOCK IS ASSEMBLED FROM FOUR THINGS ALREADY ON DISK, none of which
# costs anything on the poll:
#
#   VERSION                 the released version, committed in the release commit
#   git, in /work           commits past the tag, and whether the tree is dirty
#   .update-state           what update-check last saw at origin: the newest
#                           release, and the ordered list of newer ones
#   .wd-local/drift.json    what `make drift` last found differing on the
#                           gateways themselves
#
# THE LAST TWO ARE CACHES, AND THAT IS THE DESIGN, not a shortcut. Both answers
# cost a network round trip or minutes of gateway calls -- update-check.sh
# already splits LOOK (a timer) from SHOW (everything else, offline, instant)
# for exactly that reason, and a drift sweep is 40-60 browser logins. So this
# SHOWS. It never looks. Each block carries its own `at`, so the page can say
# how old the answer is rather than presenting a stale one as current -- the
# same rule the readiness snapshot follows.
#
# IT RUNS ON A BACKGROUND CLOCK. `/state` is on a 3-second poll and never runs a
# subprocess (see readiness()); git is a subprocess, so it goes in a thread with
# its own TTL and the poll serves whatever is cached.
STACKVER = {"at": 0, "doc": {}, "error": ""}
STACKVER_TTL = 60
STACKVER_LOCK = threading.Lock()


def _git(*args) -> str:
    """git in /work, or "" if it cannot answer.

    -c safe.directory: this container runs as root while the checkout is owned
    by the host user, and git 2.35+ refuses a repo it does not own -- on stderr,
    exit 128, which reads as "not a git repository".

    --no-optional-locks: the repo is mounted READ-ONLY on purpose, and a plain
    `git status` wants to refresh .git/index. Without this the chip would read
    "unknown" on a perfectly ordinary machine.
    """
    try:
        r = subprocess.run(["git", "-c", f"safe.directory={REPO}",
                            "--no-optional-locks", "-C", str(REPO), *args],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _update_state() -> dict:
    """.update-state, PARSED not sourced -- the log block is built from release
    summaries and sourcing that is a way to execute one. Flat KEY=VALUE, then a
    `--` line, then one line per newer release: `v1.1.0  <summary>`."""
    out = {"keys": {}, "newer": []}
    p = REPO / ".update-state"
    try:
        text = p.read_text()
    except Exception:
        return out
    body = False
    for line in text.splitlines():
        if line == "--":
            body = True
            continue
        if not body:
            k, _, v = line.partition("=")
            if k:
                out["keys"][k] = v
        elif line.strip():
            ver, _, summary = line.strip().partition("  ")
            out["newer"].append({"version": ver, "summary": summary.strip()})
    return out


def refresh_stack_version() -> None:
    doc, error = {}, ""
    try:
        version = ""
        try:
            version = (REPO / "VERSION").read_text().strip()
        except Exception:
            pass

        ahead, dirty = 0, False
        if version:
            if _git("rev-parse", "-q", "--verify", f"refs/tags/v{version}"):
                n = _git("rev-list", "--count", f"v{version}..HEAD")
                ahead = int(n) if n.isdigit() else 0
            dirty = bool(_git("status", "--porcelain"))
        else:
            dirty = bool(_git("status", "--porcelain"))

        st = _update_state()
        behind = st["keys"].get("BEHIND", "0")
        behind = int(behind) if behind.isdigit() else 0

        # MODIFIED IS THE ONE THING THE CHIP IS REALLY FOR. Two independent
        # sources, both meaning "an update would overwrite something you did":
        # the repo tree, and the gateways. Plain words, short -- they are
        # rendered in a tooltip.
        why = []
        if dirty:
            why.append("this checkout has uncommitted edits")
        drift_at, drift = 0, []
        try:
            dj = json.loads((REPO / ".wd-local" / "drift.json").read_text())
            drift_at = int(dj.get("at", 0) or 0)
            for d in dj.get("drift", []):
                n = len(d.get("settings", []) or [])
                gw = d.get("gateway", "a gateway")
                what = d.get("what", "settings")
                why.append("%d %s on %s" % (n, what, gw) if n
                           else "%s on %s" % (what, gw))
            drift = dj.get("drift", [])
        except Exception:
            # No snapshot is a legitimate state -- nobody has run `make drift`.
            # It must not read as "no drift"; `driftAt: 0` is how the page
            # tells "clean" from "never looked".
            pass

        doc = {
            "version": f"v{version}" if version else "unreleased",
            "tag": f"v{version}" if version else "",
            "commitsAhead": ahead,
            "dirty": dirty,
            "modified": bool(why),
            "modifiedWhy": why,
            "latest": st["keys"].get("LATEST", ""),
            "behind": behind,
            "newer": st["newer"],
            "track": st["keys"].get("TRACK", ""),
            "checkedAt": int(st["keys"].get("CHECKED_AT", 0) or 0),
            "driftAt": drift_at,
            "driftCount": len(drift),
            "appliedAt": _mtime(REPO / ".update-applied"),
            "migratedAt": _mtime(REPO / ".migrations-applied"),
        }
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    with STACKVER_LOCK:
        STACKVER.update(at=int(time.time()), doc=doc, error=error)


def _mtime(p) -> int:
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return 0


def stack_version_block() -> dict:
    """For /state. Always a dict with `version` in it -- NEVER omitted and never
    a bare error. An unbound sub-path renders a Perspective component's error
    box, so the page needs every key to exist whatever went wrong here."""
    with STACKVER_LOCK:
        doc = dict(STACKVER["doc"])
        doc["at"] = STACKVER["at"]
        doc["error"] = STACKVER["error"]
    doc.setdefault("version", "unknown")
    doc.setdefault("modified", False)
    doc.setdefault("modifiedWhy", [])
    doc.setdefault("behind", 0)
    doc.setdefault("newer", [])
    doc.setdefault("latest", "")
    doc.setdefault("commitsAhead", 0)
    doc.setdefault("dirty", False)
    doc.setdefault("appliedAt", 0)
    doc.setdefault("driftAt", 0)
    return doc


def stackver_loop() -> None:
    while True:
        with STACKVER_LOCK:
            due = time.time() - STACKVER["at"] > STACKVER_TTL
        if due:
            refresh_stack_version()
        time.sleep(10)


# ONE /state AT A TIME, SHARED. Measured 18/09/2026 with the host at load 29-47:
# a readiness run's node processes took the CPU, each /state slowed past the
# page's 3-second poll, the next polls arrived while it was still computing,
# and a dozen threads ended up building the same document -- every one of them
# slower for the others. No lock was held; the thread dump showed only file
# reads and `docker ps`. So concurrent callers now share one computation, and a
# document under STATE_FRESH seconds old is served as it stands.
STATE_FRESH = 2
STATE_CACHE = {"at": 0.0, "doc": None}
STATE_LOCK = threading.Lock()


def state_shared() -> dict:
    with STATE_LOCK:
        if STATE_CACHE["doc"] is None or \
                time.time() - STATE_CACHE["at"] >= STATE_FRESH:
            STATE_CACHE.update(doc=state(), at=time.time())
        return STATE_CACHE["doc"]


def state() -> dict:
    d = demos()
    up = running_stacks()
    want = desired()
    ui = stack_ui()

    def stack_view(name):
        containers = sorted(up.get(name, []), key=lambda c: c["name"])
        states = {c["health"] for c in containers}
        where = ui.get(name, {})
        return {"stack": name, "up": bool(containers), "containers": containers,
                # Where to click. Sent whether or not the stack is up: the page
                # decides whether to offer it, and a button that vanishes when
                # a stack is starting is a button people click twice.
                "url": where.get("url"), "open": where.get("open"),
                "gateway": where.get("gateway", False),
                # None, not 0. "This stack has never run here" and "this stack
                # costs nothing" are different answers and the page says so.
                "mem": (MEMORY.get(name) or {}).get("bytes"),
                "health": ("starting" if "starting" in states
                           else "unhealthy" if "unhealthy" in states
                           else "healthy" if states <= {"healthy", "up"} and states
                           else "down")}

    def cost(stacks):
        """What a set of stacks costs, and how much of that is guesswork.

        `unknown` is the honest half: a demo with one never-run stack has a
        figure that is a floor, not a total, and a card that rounds that off to
        a confident number is the reason nobody trusts the number.
        """
        known = [s["mem"] for s in stacks if s["mem"]]
        return {"mem": sum(known), "mem_known": len(known),
                "mem_unknown": len(stacks) - len(known)}

    # Which stacks a WANTED demo is holding up, and which demo is holding them.
    # A demo can be fully up without anybody having asked for it -- both edges
    # belong to three demos -- and "running" and "running because something else
    # needs it" are different answers to the only question the card is asked.
    held = {}
    by_id_all = {x["id"]: x for x in d["demos"]}
    for demo_id in want:
        for s in (by_id_all.get(demo_id) or {}).get("stacks", []):
            held.setdefault(s, []).append(demo_id)

    # What is in flight, said once for every card. BUSY IS A PROPERTY OF THE
    # RIG, not of a demo: the job lock is one per machine, so while anything is
    # working a click on any card is refused -- and a button that is refused is
    # a button that should have been disabled. `busyWhat` is the job's own words
    # ("starting eam"), which is the sentence a disabled button can show.
    gws_all = set(gateway_stacks())
    busy = JOB["state"] == "working"
    busy_what = str(JOB["what"] or "") if busy else ""

    def readiness(x, stacks):
        """(ready, why) for one demo. NO subprocess, NO network: this is on the
        3-second poll, and everything it reads is already in hand.

        The order is deliberate -- the cheap structural facts first, because
        they are free and they are the ones the page gets wrong today, and the
        readiness verdict only once the containers cannot explain the answer.
        """
        if busy and x["id"] in (JOB["demos"] or []):
            return False, busy_what

        def naming(names, one, many):
            return f"{', '.join(names)} {one if len(names) == 1 else many}"

        down = [s["stack"] for s in stacks if not s["up"]]
        if len(down) == len(stacks):
            return False, "not started"
        if down:
            return False, naming(down, "is not running", "are not running")
        bad = [s["stack"] for s in stacks if s["health"] == "unhealthy"]
        if bad:
            return False, naming(bad, "is unhealthy", "are unhealthy")
        coming = [s["stack"] for s in stacks if s["health"] == "starting"]
        if coming:
            return False, naming(coming, "is still starting",
                                 "are still starting")
        # Healthy containers, and still possibly nothing served. This is
        # finding 5: `health-check.sh -s RUNNING` passes all the way through
        # COMMISSIONING, so the gateway is asked itself.
        for s in stacks:
            problem = cached_serving_problem(s["stack"])
            if problem:
                return False, problem
        seen = None
        with READY_LOCK:
            if x["id"] in READY:
                seen = dict(READY[x["id"]])
        shape = demo_shape(x["id"], up, by_id_all, gws_all)
        if not seen or seen["shape"] != shape:
            # Never checked here, or checked against a machine in another
            # shape. Both are "not known to be ready", which is not the same
            # sentence as "not ready" and says so.
            queue_ready([x["id"]])
            return False, "checking whether it is ready"
        if int(time.time()) - int(seen["at"]) > READY_TTL:
            # Stale: refresh behind the page, but keep the verdict standing.
            # A tab that closes itself mid-demonstration because a cache
            # expired is a worse failure than a verdict with an age on it.
            queue_ready([x["id"]])
        return bool(seen["ok"]), str(seen["why"])

    def demo_view(x):
        stacks = [stack_view(s) for s in x["stacks"]]
        covered_by = sorted({o for s in x["stacks"] if up.get(s)
                             for o in held.get(s, []) if o != x["id"]})
        n_up = len([s for s in stacks if s["up"]])
        if n_up == 0:
            live = "stopped"
        elif n_up < len(stacks):
            live = "partial"
        elif any(x["health"] == "starting" for x in stacks):
            # Every container exists and at least one is not serving yet. This
            # is the state a demo spends its first minute in, and calling it
            # "running" is how somebody clicks through to a gateway that is not
            # answering and concludes the demo is broken.
            live = "starting"
        elif any(x["health"] == "unhealthy" for x in stacks):
            live = "unhealthy"
        else:
            live = "running"
        ready, ready_why = readiness(x, stacks)
        with READY_LOCK:
            ready_at = int((READY.get(x["id"]) or {}).get("at") or 0)
        return {
            "id": x["id"], "title": x["title"], "blurb": x.get("blurb", ""),
            "page": x.get("page", ""), "stacks": stacks,
            # Which console tab shows this demo, "" for one that has none.
            # Straight through from demos.json -- the manifest owns the
            # pairing, this just carries it.
            "tab": x.get("tab", ""),
            "wanted": x["id"] in want,
            # `live` is what Docker says; `wanted` is what you asked for. They
            # differ while a job runs, and the difference is the honest thing to
            # show -- a card that flips to "running" on the click is lying for
            # the forty seconds a gateway takes to start.
            "live": live,
            # A job is in flight: every action on this card would be refused,
            # and `busyWhat` is what to say instead of letting it be pressed.
            "busy": busy, "busyWhat": busy_what,
            # Would this demonstration WORK if you clicked through it now --
            # which is not `live`, and not container health. `readyWhy` is a
            # sentence either way: "ready -- 15 checks passed", "not started",
            # "ignition-edge1 is stuck in COMMISSIONING ...". `readyAt` is when
            # the verdict was taken, 0 for one that has no run behind it, so
            # the page can put an age on a snapshot rather than imply it is
            # live.
            "ready": ready, "readyWhy": ready_why, "readyAt": ready_at,
            "covered_by": covered_by,
            "stack_count": len(stacks), "stacks_up": n_up,
            **cost(stacks),
        }

    core = [stack_view(s) for s in d["core"]["stacks"]]
    # Everything of ours that is up, counted once however many demos want it --
    # which is the number that answers "what is this machine costing me now".
    # running_stacks() already answers only for containers carrying our label,
    # so this needs no second filter against stacks/.
    running = [stack_view(s) for s in sorted(up)]
    return {
        "ok": True,
        "core": {"stacks": core, "why": d["core"].get("why", ""),
                 "up": len([s for s in core if s["up"]]), "count": len(core),
                 **cost(core)},
        "demos": [demo_view(x) for x in d["demos"]],
        "running": cost(running),
        "host": host_memory(),
        "ignition": _ignition_block(up),
        "job": job_snapshot(),
        "refused": dict(REFUSED),
        # The last readiness run, with its age, or nulls if nobody has asked.
        # Carried on /state so the page needs one poll, not two.
        "verify": verify_snapshot(),
        # THE WIRE's feed (control/wire.py): broker, counters, last error.
        "wire": wire.snapshot(),
        # MQTT cuts (control/mqttcut.py): what is off wd-mqtt, until when.
        "mqtt": mqttcut.snapshot(),
        # The redundancy proof (control/redproof.py): each changeover of the
        # hub pair, timed from outside it, and the seconds its historians hold.
        "redundancy": redproof.snapshot(),
        # Which release this machine is on, whether anything here has been
        # changed since, and how many releases are waiting. Assembled on a
        # background clock from VERSION, git, .update-state and the drift
        # snapshot -- see stack_version_block().
        "version": stack_version_block(),
        "generated_at": int(time.time()),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The poller gave up before we answered -- a Perspective binding
            # that timed out. Nothing to tell it; hundreds of tracebacks in the
            # log hid the one error that mattered (EMFILE, see state_shared).
            pass

    def _send_html(self, code: int, title: str, body: str,
                   refresh: int = 0) -> None:
        """A failure a human is looking at, in a tab they just opened.

        The login route is reached by a browser, not by the page's JavaScript,
        so a JSON error body would render as raw JSON in a new tab. Say what
        went wrong and what to do instead.
        """
        html = error_page.render(title, body, refresh).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)
        # True so a caller can read "this request is answered" from the call
        # itself: `if self._not_serving(...): return`.
        return True

    CONSOLE = ("https://console.test/data/perspective/client/GatewayAdmin")

    def _not_serving(self, stack: str, host: str) -> bool:
        """True when a page was served instead of signing in.

        Two different answers, because they need two different sentences: the
        demo has not been started, or it has and the gateway is not serving
        yet. Telling somebody to start a demo that is already starting is how
        they press Start, get a 409, and conclude the console is broken.
        """
        try:
            d = demos()
            core = set(d["core"]["stacks"])
            owners = [x for x in d["demos"] if stack in x["stacks"]]
        except Exception:  # noqa: BLE001 -- an unreadable manifest must still
            d, core, owners = None, set(), []   # produce a page, not a stack trace

        if stack not in running_stacks():
            if stack in core:
                return self._send_html(
                    503, f"{stack} is not running",
                    f"<p><code>{stack}</code> is part of the CORE, which is "
                    "meant to be up -- so this is a fault rather than a stopped "
                    f"demonstration.</p><p><code>make up STACK={stack}</code></p>")
            if not owners:
                return self._send_html(
                    503, f"{stack} is not running",
                    f"<p><code>{stack}</code> is stopped, and no demonstration "
                    "in <code>demos.json</code> lists it -- so nothing starts it "
                    f"for you.</p><p><code>make up STACK={stack}</code></p>")
            one, rest = owners[0], owners[1:]
            title = esc(one["title"])
            also = (" It also belongs to "
                    + ", ".join(esc(x["title"]) for x in rest)
                    + ".") if rest else ""
            return self._send_html(
                503, f"The {title} is not running",
                f"<p><code>{esc(host)}</code> is the front door of "
                f"<code>{esc(stack)}</code>, which the <b>{title}</b> starts."
                f"{also}</p>"
                f"<p>Press <b>Start</b> on the <b>{title}</b> card on the "
                f"<a href='{self.CONSOLE}'>demo console's Demos tab</a>, wait for "
                "the card to read <b>Running</b>, then open this again.</p>"
                f"<p>From a terminal: <code>make demo-start DEMO="
                f"{esc(one['id'])}</code></p>"
                "<p>Nothing is wrong with the proxy or the gateway -- the "
                "demonstration this gateway belongs to has not been started.</p>")

        problem = serving_problem(stack, probe_ping(stack))
        if problem and "COMMISSIONING" in problem:
            return self._send_html(
                503, f"{stack} is waiting to be commissioned",
                f"<p>{problem}.</p><p>Signing in would land on the wizard, so "
                "nothing was changed here. This one does not clear itself.</p>")
        if problem:
            # The only case that fixes itself, so it is the only one that
            # reloads. A gateway is up within a second and serves nothing for
            # another minute.
            return self._send_html(
                503, f"{stack} is still starting",
                f"<p>{problem} -- an Ignition gateway accepts connections about "
                "a second in and serves its projects a minute later.</p>"
                "<p>This page reloads every ten seconds and will take you "
                "there.</p>", refresh=10)
        return False

    def _login(self, query: dict) -> None:
        """Establish a gateway session and hand it to the browser.

        Reached as https://<gateway>.test/_wd/login?next=/web/home -- nginx
        proxies /_wd/ here, so the Host header is the gateway being asked for
        and the Set-Cookie below lands on that origin. That is the whole trick:
        the cookies are only usable because the response comes back through the
        gateway's own name.
        """
        host = (self.headers.get("Host") or "").split(":")[0]
        known = autologin.gateway_stacks(str(REPO))
        gw = known.get(host)
        if not gw:
            return self._send_html(
                404, "Not a gateway",
                # Escaped: this is the one value here that comes straight from
                # a request header.
                f"<p><code>{esc(host)}</code> is not one of this stack's "
                "Ignition gateways, so there is no login to perform here.</p>")

        # IS THERE A GATEWAY THERE AT ALL? Asked before the credentials are
        # touched, because otherwise this route answers a stopped demo with
        # "could not reach the gateway at https://edge1.test (HTTPError)" -- a
        # protocol failure, in a tab somebody opened from a demo card, about a
        # machine that is doing exactly what was asked of it.
        #
        # The proxy already has the right page for this and generates it from
        # demos.json (stacks/npm/gen-down-page.py), but a gateway's chip never
        # reaches it: the chip points at /_wd/login, which nginx proxies HERE,
        # so this answers before the down page can. So this says the same
        # thing, from the same manifest.
        if self._not_serving(gw["stack"], host):
            return

        # Only ever a path on this same host. `next` arrives from a link, and a
        # link is something a person can retype: without this an Open button
        # could be turned into an open redirect off the gateway's own origin.
        nxt = query.get("next", ["/web/home"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/web/home"

        # THROUGH THE FRONT DOOR, not http://<container>:8088. An Ignition
        # session is bound to the origin it was minted on -- measured: a session
        # created on http://localhost:8090 and presented on https://console.test
        # answers 401, with the cookies present and correct. The gateway also
        # renames its cookies under HTTPS (see autologin.login), so the scheme
        # has to match too. Every .test name is a network alias of the npm
        # container on `backbone`, so this resolves in here without any DNS of
        # our own -- see stacks/npm/compose.yaml.
        base = f"https://{host}"
        try:
            cookies = autologin.login(base, *autologin.credential(*(
                (gw["stanza"], str(REPO / ".gateways.env")))))
        except autologin.LoginError as e:
            return self._send_html(
                502, "Could not sign in",
                f"<p>{e}</p><p>Opening <code>{host}</code> anyway would just "
                "show the login page, so nothing was changed. "
                f"<a href='{nxt}'>Go there and log in by hand</a>.</p>")
        except Exception as e:  # noqa: BLE001
            return self._send_html(
                502, "Could not sign in",
                f"<p>Unexpected {type(e).__name__} talking to "
                f"<code>{gw['stack']}</code>.</p>")

        self.send_response(302)
        self.send_header("Location", nxt)
        for c in cookies:
            # Re-issue what the gateway sent, with its own name and path.
            #
            # The names carry cookie PREFIXES under HTTPS -- `__Host-` and
            # `__Secure-` -- and a browser silently DROPS a prefixed cookie that
            # breaks the prefix's rules. `__Host-` demands Secure, Path=/ and no
            # Domain; `__Secure-` demands Secure. So: always Secure, never a
            # Domain (host-only is what we want anyway, each gateway keeps its
            # own session), and Path=/ forced for a __Host- cookie rather than
            # trusted. A dropped cookie here would look exactly like a login
            # that silently failed.
            path = "/" if c["name"].startswith("__Host-") else c["path"]
            # HttpOnly because nothing in the page has any business reading a
            # session cookie; SameSite=Lax so it is still sent on the top-level
            # navigation this redirect is about to perform.
            self.send_header(
                "Set-Cookie",
                f"{c['name']}={c['value']}; Path={path}; "
                "HttpOnly; Secure; SameSite=Lax")
        self.send_header("Content-Length", "0")
        # A browser that re-fetches this from cache would present a dead session.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path, _, raw = self.path.partition("?")
        query = urllib.parse.parse_qs(raw)
        if path == "/healthz":
            return self._send(200, {"ok": True})
        if path == "/state":
            return self._send(200, state_shared())
        if path == "/verify":
            return self._send(200, verify_snapshot())
        if path == "/login":
            return self._login(query)
        if path == "/trials":
            return self._send_html(200, "Gateway trials", trialkeeper.page())
        return self._send(404, {"error": "no such resource"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            known = {x["id"] for x in demos()["demos"]}
            # The desired set is computed inside the lambda, not before it, so it
            # is read as well as written under the lock -- otherwise two clicks
            # arriving together could both read the old list and the second
            # would undo the first.
            if self.path == "/trials/reset":
                # A form post from the Trials page, then back to it.
                n = int(self.headers.get("Content-Length") or 0)
                form = urllib.parse.parse_qs(self.rfile.read(n).decode() if n else "")
                trialkeeper.reset((form.get("gateway") or [""])[0])
                self.send_response(303)
                self.send_header("Location", "../trials")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/verify":
                # Fire and answer. A readiness run takes tens of seconds and the
                # page is already polling /state -- blocking the click on it
                # would give a frozen button for the same information, arriving
                # later. Same shape as start_job for the same reason.
                threading.Thread(target=run_verify, daemon=True).start()
                return self._send(202, {"started": True})
            m = re.match(r"^/ignition-version/([0-9][0-9.]*)$", self.path)
            if m:
                want = m.group(1)
                # Offered-only. The script re-checks everything; this stops the
                # endpoint being a way to ask for a version the picker would
                # never have shown -- an unauthenticated control plane should
                # not widen what a click can do.
                allowed = _ignition_block(running_stacks())["available"]
                if want not in allowed:
                    return self._send(400, {
                        "ok": False,
                        "error": f"{want} is not on offer here",
                        "available": allowed})
                return self._send(*start_version_job(want))
            if self.path == "/redundancy/request":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                except ValueError:
                    return self._send(400, {"ok": False, "why": "the body was not JSON"})
                return self._send(*redproof.request(body.get("kind")))
            if self.path in ("/mqtt/cut", "/mqtt/restore"):
                return self._send(*self._mqtt(self.path.rsplit("/", 1)[1]))
            if self.path == "/stop-all":
                return self._send(*start_job("stopping every demo",
                                             lambda: set_desired([]),
                                             subjects=desired()))
            m = re.match(r"^/demos/([a-z0-9-]+)/(start|stop)$", self.path)
            if m:
                demo_id, action = m.group(1), m.group(2)
                if demo_id not in known:
                    return self._send(404, {"error": f"no such demo: {demo_id}"})

                def mutate(demo_id=demo_id, action=action):
                    if (action == "start" and demo_id == "redundancy"
                            and demo_id not in desired()):
                        redproof.clear()
                    want = [x for x in desired() if x != demo_id]
                    if action == "start":
                        want.append(demo_id)
                    set_desired(want)

                verb = "starting" if action == "start" else "stopping"
                return self._send(*start_job(f"{verb} {demo_id}", mutate,
                                             subjects=[demo_id]))
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "no such resource"})

    def _mqtt(self, verb: str) -> tuple:
        """The two token-protected MQTT actions. Restore is never refused as
        busy: putting an edge back is the safe direction, whatever else runs."""
        status, reply = mqttcut.authorise(self.headers.get("X-WD-Token", ""))
        if status:
            return status, reply
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return 400, {"ok": False, "why": "the body was not JSON"}
        if verb == "cut":
            busy = JOB["what"] if JOB_LOCK.locked() else ""
            return mqttcut.cut(body.get("stack"),
                               body.get("seconds", mqttcut.DEFAULT_SECONDS), busy)
        return mqttcut.restore(body.get("stack"))

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"wd-control on :{PORT}, repo {REPO}", flush=True)
    # migrations/1.1.6-01 looks for this line to know demo starts converge.
    print("converge: demo starts re-apply gateway settings, logged to .wd-local/converge-last.log", flush=True)
    # `docker kill -s USR1 wd-control` prints every thread's stack to the log.
    # The image has no py-spy or gdb, and "/state hangs while /healthz answers"
    # is a lock problem that nothing else in here can show you.
    import faulthandler
    import signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    load_memory()
    # Sampled on a timer rather than inside /state: `docker stats` costs a
    # second or so even for six containers, and the page polls.
    threading.Thread(target=memory_loop, daemon=True).start()
    # The Ignition tag list, on its own clock for the same reason: a network
    # call to Docker Hub has no business on a 3-second page poll.
    threading.Thread(target=tags_loop, daemon=True).start()
    threading.Thread(target=stackver_loop, daemon=True).start()
    # Is each running gateway SERVING, as opposed to healthy. Its own clock for
    # the same reason: one `docker exec` per gateway has no business on a
    # 3-second page poll.
    threading.Thread(target=ping_loop, daemon=True).start()
    # Readiness, and the one line that makes it not opt-in. A restart of this
    # container loses the verdicts (they are about a machine, not a wish), so
    # the demos that were already wanted are checked as soon as it comes up --
    # after a pause, because everything else is starting at the same moment and
    # a readiness run is 40-60 seconds of docker exec.
    threading.Thread(target=ready_loop, daemon=True).start()
    # THE WIRE's MQTT listener: its own two threads, a bounded queue, and
    # nothing shared with the HTTP service but wire.snapshot().
    wire.start()
    # MQTT cuts: restores anything overdue from before a restart, then keeps
    # every cut's deadline.
    mqttcut.start()
    # The redundancy proof: watches both halves of the hub pair, records each
    # changeover, then asks both historians what they kept.
    redproof.start()
    threading.Timer(45, queue_ready, args=(desired(),)).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
