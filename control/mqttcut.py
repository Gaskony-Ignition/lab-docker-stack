"""Cutting one edge off MQTT, and putting it back on time.

The Sparkplug tab's "Cut Edge 3 link" and the Store & Forward tab's MQTT
break both need "the WAN to the broker went away" for one edge. The broker is
MQTT Distributor on the hub pair, which has no per-client kick: a user change
restarts the whole broker (docs/MQTT-DISTRIBUTOR.md, T-D12). So the cut is
the network: every MQTT client dials the broker on `wd-mqtt`, a network that
carries MQTT only, and `docker network disconnect wd-mqtt <edge>` takes that
road away while `backbone` stays, so the console's observer still answers.

The gateway can't run docker; this container can. So the page asks here:

  POST /mqtt/cut      {"stack": "ignition-edge3", "seconds": 60}
  POST /mqtt/restore  {"stack": "ignition-edge3"}

both with `X-WD-Token` (WD_CONTROL_TOKEN in .secrets.env; the hub holds the
same value as the `wd-control-token` secret, installed by ign-secrets.sh).

EVERY CUT IS TIME-BOXED HERE, not by the page. The deadline is written to
/state/mqtt-cuts.json before the disconnect, and a loop restores anything
overdue -- including after a restart of this container, which is the case a
timer in memory would lose, leaving an edge off the broker until somebody
noticed. The reconnect repeats the aliases compose gave the container on
wd-mqtt, so it comes back exactly as `up` made it.
"""
from __future__ import annotations

import hmac
import json
import os
import pathlib
import subprocess
import threading
import time

NET = "wd-mqtt"
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
CUTS_FILE = STATE_DIR / "mqtt-cuts.json"
SECRETS_FILE = pathlib.Path(os.environ.get("WD_SECRETS_FILE", "/work/.secrets.env"))
STACK_LABEL = "au.gaskony.wd.stack"
# Only the edges. The broker halves are on wd-mqtt too, and cutting one of
# those is a hub failover, which has its own demo and its own button.
CUTTABLE = [s.strip() for s in os.environ.get(
    "WD_MQTT_CUTTABLE", "ignition-edge2,ignition-edge3,ignition-edge4").split(",") if s.strip()]
MIN_SECONDS, MAX_SECONDS = 5, 600
# How long a cut lasts when the caller does not say. A knob a machine may keep
# across an update -- demo-settings.env.example, and the table in
# docs/RELEASING.md -- passed in by compose from stacks/wd-control/.env.
#
# THE CONSOLE'S BUTTONS PASS THEIR OWN NUMBER, so this is the floor the control
# plane falls back to rather than the value the page uses. A Perspective event
# script cannot read a shell variable; for the page to honour this, its buttons
# would have to take the default from /state's `mqtt.defaultSeconds` instead of
# a literal, which is a change in the console's own project.
try:
    DEFAULT_SECONDS = int(os.environ.get("WD_MQTT_CUT_SECONDS", "60"))
except ValueError:
    DEFAULT_SECONDS = 60
DEFAULT_SECONDS = max(MIN_SECONDS, min(MAX_SECONDS, DEFAULT_SECONDS))
SAMPLE_EVERY = 5          # s between `docker network inspect` samples

_lock = threading.Lock()
_cuts: dict = {}          # stack -> {"until", "since", "container", "aliases", "by"}
_members: dict = {"at": 0, "names": set(), "error": ""}
_last = {"what": "", "at": 0}


def _log(msg: str) -> None:
    print(f"mqtt-cut {time.strftime('%H:%M:%S')}: {msg}", flush=True)


def _docker(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _save() -> None:
    tmp = CUTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cuts, indent=1))
    tmp.replace(CUTS_FILE)


def _load() -> None:
    try:
        doc = json.loads(CUTS_FILE.read_text())
        if isinstance(doc, dict):
            _cuts.update(doc)
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001 -- a corrupt file must not stop the service
        _log(f"could not read {CUTS_FILE}: {type(e).__name__}: {e}")


# --- the token ----------------------------------------------------------------

def _token() -> str:
    """WD_CONTROL_TOKEN, from the environment or .secrets.env. Read per request,
    so `make env` takes effect without a restart. Never logged."""
    v = os.environ.get("WD_CONTROL_TOKEN", "").strip()
    if v:
        return v
    try:
        for line in SECRETS_FILE.read_text().splitlines():
            k, sep, val = line.partition("=")
            if sep and k.strip() == "WD_CONTROL_TOKEN":
                return val.strip()
    except OSError:
        pass
    return ""


def authorise(header: str) -> tuple:
    """(None, None) when the caller may act, else (status, reply)."""
    want = _token()
    if not want:
        return 503, {"ok": False, "why": "wd-control has no WD_CONTROL_TOKEN, so it "
                     "accepts no MQTT cuts -- run: make env, then scripts/ign-secrets.sh"}
    if not header or not hmac.compare_digest(header.strip(), want):
        return 403, {"ok": False, "why": "that request did not carry wd-control's token"}
    return None, None


# --- docker ---------------------------------------------------------------------

def _container(stack: str) -> str:
    cp = _docker("ps", "-a", "--filter", f"label={STACK_LABEL}={stack}", "--format", "{{.Names}}")
    names = [n for n in cp.stdout.split() if n]
    return names[0] if names else ""


def _sample() -> set:
    """Who is on wd-mqtt now, by container name."""
    cp = _docker("network", "inspect", NET, "--format",
                 "{{range .Containers}}{{.Name}} {{end}}")
    with _lock:
        if cp.returncode != 0:
            _members.update(at=time.time(), error=(cp.stderr or "").strip()[:200])
        else:
            _members.update(at=time.time(), names=set(cp.stdout.split()), error="")
        return set(_members["names"])


def _aliases(container: str) -> list:
    cp = _docker("inspect", container, "--format",
                 "{{json (index .NetworkSettings.Networks \"%s\").Aliases}}|"
                 "{{index .Config.Labels \"com.docker.compose.service\"}}" % NET)
    raw, _, service = cp.stdout.strip().partition("|")
    out = []
    try:
        out = [a for a in (json.loads(raw) or []) if a != container]
    except ValueError:
        pass
    if service and service not in out:
        out.append(service)
    return sorted(set(out))


def _reconnect(stack: str, rec: dict) -> str:
    """'' on success, else the reason in plain words."""
    container = rec.get("container") or _container(stack)
    if not container:
        return f"{stack} has no container any more, so there is nothing to reconnect"
    args = ["network", "connect"]
    for a in rec.get("aliases") or []:
        args += ["--alias", a]
    cp = _docker(*args, NET, container)
    err = (cp.stderr or "").strip()
    if cp.returncode != 0 and "already exists" not in err and "already attached" not in err:
        return f"docker could not reconnect {container} to {NET}: {err[:160]}"
    return ""


# --- the actions -----------------------------------------------------------------

def _left(rec: dict, now: float) -> int:
    return max(0, int(round(rec["until"] - now)))


def cut(stack: str, seconds, busy_what: str = "") -> tuple:
    """(status, reply). Refusals are 409 with `why` in plain words."""
    stack = str(stack or "").strip()
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return 400, {"ok": False, "why": "seconds must be a whole number"}
    if stack not in CUTTABLE:
        return 400, {"ok": False, "why": f"{stack or 'that'} is not an edge this console "
                     f"cuts off MQTT (it cuts {', '.join(CUTTABLE)})"}
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        return 400, {"ok": False, "why": f"a cut lasts {MIN_SECONDS} to {MAX_SECONDS} seconds"}
    if busy_what:
        return 409, {"ok": False, "why": f"cutting {stack} was ignored: {busy_what} is "
                     "still running, and this rig does one job at a time."}
    now = time.time()
    with _lock:
        rec = _cuts.get(stack)
        if rec and rec["until"] > now:
            return 409, {"ok": False, "why": f"{stack} is already cut off -- it comes back "
                         f"in {_left(rec, now)}s", "until": rec["until"]}
    container = _container(stack)
    if not container:
        return 409, {"ok": False, "why": f"{stack} is not running, so there is nothing to cut"}
    if container not in _sample():
        return 409, {"ok": False, "why": f"{container} is not on {NET}, so it has no MQTT "
                     "link to cut -- is its demo running?"}
    rec = {"until": now + seconds, "since": now, "container": container,
           "aliases": _aliases(container), "seconds": seconds}
    # Deadline on disk BEFORE the disconnect: a crash between the two must
    # still end in a restore, never in an edge left off the broker.
    with _lock:
        _cuts[stack] = rec
        _save()
    cp = _docker("network", "disconnect", NET, container)
    if cp.returncode != 0:
        with _lock:
            _cuts.pop(stack, None)
            _save()
        return 500, {"ok": False, "why": f"docker could not disconnect {container}: "
                     f"{(cp.stderr or '').strip()[:160]}"}
    _sample()
    _last.update(what=f"cut {stack} for {seconds}s", at=int(now))
    _log(f"cut {container} off {NET} for {seconds}s (restores at "
         f"{time.strftime('%H:%M:%S', time.localtime(rec['until']))})")
    return 200, {"ok": True, "stack": stack, "until": rec["until"], "secondsLeft": seconds}


def restore(stack: str, why: str = "by hand") -> tuple:
    stack = str(stack or "").strip()
    if stack not in CUTTABLE:
        return 400, {"ok": False, "why": f"{stack or 'that'} is not an edge this console cuts"}
    with _lock:
        rec = dict(_cuts.get(stack) or {})
    if not rec:
        container = _container(stack)
        # Nothing recorded, but off the network all the same (a cut from an
        # older wd-control, or by hand): put it back rather than refuse.
        if container and container not in _sample():
            rec = {"container": container, "aliases": [stack]}
        else:
            return 409, {"ok": False, "why": f"{stack} is not cut off"}
    problem = _reconnect(stack, rec)
    with _lock:
        _cuts.pop(stack, None)
        _save()
    _sample()
    if problem:
        _log(problem)
        return 500, {"ok": False, "why": problem}
    _last.update(what=f"restored {stack} ({why})", at=int(time.time()))
    _log(f"restored {rec.get('container', stack)} to {NET} ({why})")
    return 200, {"ok": True, "stack": stack}


def snapshot() -> dict:
    """For /state: what is cut, until when, and whether docker agrees."""
    now = time.time()
    with _lock:
        members = set(_members["names"])
        cuts = {s: {"until": int(r["until"]), "since": int(r.get("since", 0)),
                    "secondsLeft": _left(r, now), "seconds": r.get("seconds", 0),
                    "container": r.get("container", ""),
                    # The second opinion: docker's own membership list.
                    "offNetwork": r.get("container", "") not in members}
                for s, r in _cuts.items()}
        return {"network": NET, "cuttable": list(CUTTABLE), "cuts": cuts,
                # So the page can stop carrying a literal 60 of its own.
                "defaultSeconds": DEFAULT_SECONDS,
                "onNetwork": sorted(members), "sampledAt": int(_members["at"]),
                "error": _members["error"], "last": dict(_last)}


def _loop() -> None:
    last_sample = 0.0
    while True:
        try:
            now = time.time()
            with _lock:
                due = [s for s, r in _cuts.items() if r["until"] <= now]
            for s in due:
                restore(s, why="its time ran out")
            if now - last_sample >= SAMPLE_EVERY:
                members = _sample()
                last_sample = now
                # Recreated by compose mid-cut (a stop/start of its demo puts it
                # back on every network): the cut is over, so say so.
                with _lock:
                    back = [s for s, r in _cuts.items()
                            if r.get("container") in members and now - r.get("since", now) > 10]
                for s in back:
                    with _lock:
                        _cuts.pop(s, None)
                        _save()
                    _last.update(what=f"{s} was back on {NET} before its cut ended", at=int(now))
                    _log(f"{s} is back on {NET} (recreated?) -- cut record dropped")
        except Exception as e:  # noqa: BLE001 -- this loop is the safety net
            _log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(1)


def start() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _load()
    if _cuts:
        _log(f"found {len(_cuts)} cut(s) from before a restart: "
             + ", ".join(f"{s} until {time.strftime('%H:%M:%S', time.localtime(r['until']))}"
                         for s, r in _cuts.items()))
    threading.Thread(target=_loop, name="mqtt-cut", daemon=True).start()
