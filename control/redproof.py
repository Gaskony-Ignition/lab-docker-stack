"""The redundancy demo's proof: how fast a changeover was, and whether any
data was lost -- recorded from OUTSIDE the pair.

A handover drops the viewer's Perspective session for about 30 s, so nothing
on the page can time it. This module can: wd-control is in neither half.

  1. Every POLL seconds it reads /system/gwinfo on both halves (open, no
     credential -- the same string HAProxy routes on) and notes which half
     reports RedundantNodeActiveStatus=Active.
  2. When that moves from one half to the other it records a changeover:
     when the old half was last seen in charge, when it stopped answering or
     stood down, when the new half was first seen in charge.
  3. WINDOW_AFTER seconds later it asks BOTH halves, through the WebDev route
     /system/webdev/GatewayAdmin/redundancy/proof, which seconds of the proof
     tag ([default]Redundancy/Clock, one value a second, the value being the
     second itself) are stored for the window around it. Both halves write
     to the one Postgres historian, so either answer is the record; both are
     asked and their union taken, so a half that is down does not stop the
     count. A missing second is one nothing stored.

  POST /redundancy/request  {"kind": "handover"|"stop"}  -- the console's
       buttons and `make redundancy-failover|fail` say when they asked, so a
       planned changeover is timed from the request. Unauthenticated, like the
       rest of wd-control: it annotates the next changeover and does nothing
       else.

The last KEEP changeovers are on /state/redundancy-proof.json, pending ones
included, so a restart of this container finishes what it had started.
Standard library only.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
import urllib.request

STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
FILE = STATE_DIR / "redundancy-proof.json"
# name=host:port, master first. Container names on `backbone`, never host ports.
HALVES = []
for part in os.environ.get("WD_RED_HALVES",
                           "master=ignition:8088,backup=ignition-backup:8088").split(","):
    name, _, addr = part.strip().partition("=")
    if name and addr:
        HALVES.append((name, addr))
POLL = 0.25
PROBE_TIMEOUT = 1.0
WINDOW_BEFORE = 60
WINDOW_AFTER = 60
# How long to keep asking a half that is not answering (a master restarting
# takes about a minute) before judging with what the other half holds.
EVAL_GIVE_UP = 300
DOWN_FOR = 3              # s of silence that makes a stood-down half "gone"
REQUEST_VALID = 120       # a request annotates a changeover up to this long after it
KEEP = 10
ROUTE = "/system/webdev/GatewayAdmin/redundancy/proof"

_lock = threading.Lock()
_seen = {n: {"answering": False, "active": False, "role": "", "at": 0.0,
             "context": ""} for n, _ in HALVES}
_track = {"holder": "", "lastActiveAt": 0.0}
_runs: dict = {}          # half -> {"activeSince", "downSince"}: the current run of each
_request = {"kind": "", "at": 0.0}
_changeovers: list = []   # newest first


def _log(msg: str) -> None:
    print(f"redproof {time.strftime('%H:%M:%S')}: {msg}", flush=True)


def _save() -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_changeovers[:KEEP], indent=1))
    tmp.replace(FILE)


def _load() -> None:
    try:
        doc = json.loads(FILE.read_text())
        if isinstance(doc, list):
            _changeovers.extend(doc[:KEEP])
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001 -- a corrupt file must not stop the service
        _log(f"could not read {FILE}: {type(e).__name__}: {e}")


def _get(addr: str, path: str, timeout: float) -> bytes:
    with urllib.request.urlopen(f"http://{addr}{path}", timeout=timeout) as r:
        return r.read()


# --- watching ------------------------------------------------------------------

def _probe(name: str, addr: str) -> None:
    """One half, on its own thread, so a half that hangs cannot slow the other."""
    while True:
        t0 = time.time()
        rec = {"answering": False, "active": False, "role": "", "context": ""}
        try:
            text = _get(addr, "/system/gwinfo", PROBE_TIMEOUT).decode(errors="replace")
            kv = dict(p.split("=", 1) for p in text.strip().split(";") if "=" in p)
            rec["answering"] = True
            rec["context"] = kv.get("ContextStatus", "")
            rec["role"] = kv.get("RedundancyStatus", "")
            # In the pair AND in charge. An Independent gateway reports Active
            # too (docs/REDUNDANCY.md, the front door), and a STARTING one is
            # not serving anything yet.
            rec["active"] = (kv.get("RedundantNodeActiveStatus") == "Active"
                             and rec["role"] in ("Master", "Backup")
                             and rec["context"] == "RUNNING")
        except Exception:  # noqa: BLE001 -- not answering IS the reading
            pass
        with _lock:
            _seen[name].update(rec, at=t0)
        time.sleep(max(0.0, POLL - (time.time() - t0)))


def _watch() -> None:
    while True:
        try:
            _step(time.time())
        except Exception as e:  # noqa: BLE001 -- this loop is the recorder
            _log(f"watch error: {type(e).__name__}: {e}")
        time.sleep(POLL)


def _step(now: float) -> None:
    with _lock:
        seen = {n: dict(v) for n, v in _seen.items()}
    for n, v in seen.items():
        runs = _runs.setdefault(n, {"activeSince": 0.0, "downSince": 0.0})
        runs["activeSince"] = (runs["activeSince"] or v["at"]) if v["active"] else 0.0
        runs["downSince"] = (runs["downSince"] or v["at"]) if not v["answering"] else 0.0
    active = [n for n, v in seen.items() if v["active"]]
    holder = _track["holder"]
    if not holder:
        if len(active) == 1:
            _track.update(holder=active[0], lastActiveAt=seen[active[0]]["at"])
            _log(f"{active[0]} is in charge")
        return
    if holder in active:
        # Still in charge -- even if the other half ALSO says Active, which a
        # planned handover does for a moment (measured 22/09/2026). The
        # changeover is when the old half stops, not when the new one starts.
        _track["lastActiveAt"] = seen[holder]["at"]
        return
    others = [n for n in active if n != holder]
    if len(others) == 1:
        new = others[0]
        _record(holder, new, _runs[new]["activeSince"] or seen[new]["at"],
                _runs[holder]["downSince"])
        _track.update(holder=new, lastActiveAt=seen[new]["at"])


def _record(old: str, new: str, new_at: float, old_down_since: float) -> None:
    last_active = _track["lastActiveAt"]
    req = dict(_request)
    requested = req["at"] if req["at"] and 0 <= last_active - req["at"] <= REQUEST_VALID else 0
    # GONE, not merely slow: a half standing down in a planned handover can
    # miss a probe or two, so only a silence of DOWN_FOR seconds by the time
    # the new half is in charge counts as the old half having gone.
    gone = bool(old_down_since) and (time.time() - old_down_since) >= DOWN_FOR
    if requested and req["kind"] == "handover":
        kind = "planned"
    elif (requested and req["kind"] == "stop") or gone:
        kind = "unplanned"
    else:
        # Stood down with nobody asking: a master returning and taking its
        # responsibility back (recovery Automatic).
        kind = "automatic"
    # Timed from the REQUEST for a planned handover -- the moment the operator
    # acted. Otherwise from the last moment the old half was seen in charge:
    # "Restart this gateway" waits 5 s before it pulls the floor out, and that
    # wait is the button's, not the pair's.
    start = requested if kind == "planned" else last_active
    rec = {
        "id": int(new_at * 1000),
        "at": int(start * 1000),
        "kind": kind,
        "direction": f"{old} -> {new}",
        "from": old, "to": new,
        "requestedAt": int(requested * 1000) if requested else None,
        "requestKind": req["kind"] if requested else "",
        "oldLastActiveAt": int(last_active * 1000),
        "oldGone": gone,
        "newActiveAt": int(new_at * 1000),
        "activeAfterMs": max(0, int((new_at - start) * 1000)),
        # By this recorder's own polls: how long NEITHER half said Active, or
        # how long BOTH did (a planned handover overlaps).
        "noneActiveMs": max(0, int((new_at - last_active) * 1000)),
        "bothActiveMs": max(0, int((last_active - new_at) * 1000)),
        "state": "pending",
        # Around the moment the new half was first seen in charge, which every
        # kind shares -- the request mark can be ~15 s early (_refine).
        "window": {"from": int(new_at) - WINDOW_BEFORE, "to": int(new_at) + WINDOW_AFTER},
    }
    if requested:
        _request.update(kind="", at=0.0)
    with _lock:
        _changeovers.insert(0, rec)
        del _changeovers[KEEP:]
        _save()
    _log(f"changeover {kind} {old} -> {new}: in charge after {rec['activeAfterMs']} ms "
         f"(none in charge {rec['noneActiveMs']} ms, both {rec['bothActiveMs']} ms)")


# --- judging -------------------------------------------------------------------

def _ask(addr: str, frm: int, to: int) -> dict:
    body = _get(addr, f"{ROUTE}?from={frm}&to={to}", 15)
    d = json.loads(body.decode())
    if not d.get("ok"):
        raise RuntimeError(d.get("message") or "the route said no")
    return d


def _runs_of(seconds: list) -> list:
    out = []
    for s in sorted(int(x) for x in seconds):
        if out and s == out[-1][1] + 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return out


def _refine(rec: dict, per: dict) -> None:
    """Time the changeover from the GATEWAYS' OWN LOGS where they know better
    than this recorder's polls.

    A planned handover is a request the gateway acts on in well under a
    second (measured 22/09/2026: logged request 09:57:57.782, master Active
    09:57:57.842), while `./wd redundancy-failover` spends ~15 s starting the
    toolbox and logging in before it sends it. So the request is the moment
    the old half LOGGED it, not the moment anybody said they would ask; and
    the end is the moment the new half logged taking charge. An unplanned
    changeover has no such line on the half that died, so it stays timed
    from outside: the old half last seen in charge -> the new half first seen
    in charge, both by these polls (+-POLL)."""
    new_at = rec["newActiveAt"]
    lines = [(int(e.get("ts") or 0), half, str(e.get("text") or "").lower())
             for half, d in per.items() for e in d.get("events") or []]
    reqs = [ts for ts, _h, t in lines
            if "request initiated" in t
            and ("force-failover" in t or rec.get("requestKind") == "handover")
            and new_at - REQUEST_VALID * 1000 <= ts <= new_at + 1000]
    acts = [ts for ts, h, t in lines
            if h == rec["to"] and "redundancy state changed" in t
            and "activity level=active" in t and abs(ts - new_at) <= 15000]
    if reqs:
        rec["requestLoggedAt"] = max(reqs)
        if rec["kind"] == "automatic" and not rec.get("oldGone"):
            rec["kind"] = "planned"
    if acts:
        rec["newLoggedActiveAt"] = min(acts, key=lambda t: abs(t - new_at))
    if rec["kind"] == "planned" and (rec.get("requestLoggedAt") or rec.get("requestedAt")):
        start = rec.get("requestLoggedAt") or rec["requestedAt"]
        end = rec.get("newLoggedActiveAt") or new_at
        rec["timedBy"] = "gateway log" if rec.get("requestLoggedAt") and \
            rec.get("newLoggedActiveAt") else "recorder"
    else:
        start, end = rec["oldLastActiveAt"], new_at
        rec["timedBy"] = "recorder"
    rec["at"] = int(start)
    rec["activeAfterMs"] = max(0, int(end - start))
    rec["refined"] = True


def _judge(rec: dict) -> bool:
    """Fill in expected/stored/missing. True when the record is final."""
    frm, to = rec["window"]["from"], rec["window"]["to"]
    now = time.time()
    final = now >= to + 5
    if not final and (rec.get("refined") or now < rec["newActiveAt"] / 1000.0 + 3):
        return False
    per, errors = {}, {}
    for name, addr in HALVES:
        try:
            per[name] = _ask(addr, frm, to if final else min(to, int(now)))
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {e}"[:160]
    # Early, once: the timing from the gateways' logs, so the page has the
    # right figure long before the window closes.
    _refine(rec, per)
    if not final:
        return False
    late = now - (rec["newActiveAt"] / 1000.0) > EVAL_GIVE_UP
    if errors and not late:
        rec["waitingFor"] = sorted(errors)
        return False
    stored = set()
    for d in per.values():
        stored.update(int(s) for s in d.get("seconds") or [])
    expected = to - frm
    missing = [s for s in range(frm, to) if s not in stored]
    # The first row the NEW half stored at or after the start.
    first_new = None
    stamps = (per.get(rec["to"]) or {}).get("stamps") or {}
    for s in sorted(int(k) for k in stamps):
        if stamps[str(s)] >= rec["at"]:
            first_new = stamps[str(s)]
            break
    # WHY, second by second, from this recorder's own polls: a missing
    # second is in the stretch where neither half said Active, or in one
    # where a half said Active and stored nothing -- a JVM stalled under host
    # load, measured 22/09/2026 (docs/REDUNDANCY.md).
    none_from = rec["oldLastActiveAt"] / 1000.0
    none_to = rec["newActiveAt"] / 1000.0
    kinds = {"none": 0, "old": 0, "new": 0}
    for s in missing:
        if s + 1 <= none_from:
            kinds["old"] += 1
        elif s >= none_to:
            kinds["new"] += 1
        else:
            kinds["none"] += 1
    clauses = []
    if kinds["none"]:
        clauses.append("neither half was in charge")
    if kinds["old"]:
        clauses.append(f"the {rec['from']} was in charge and stored none of them")
    if kinds["new"]:
        clauses.append(f"the {rec['to']} was in charge and stored none of them")
    if len(clauses) > 1:
        clauses = [f"{n} while {c}" for n, c in
                   zip([kinds[k] for k in ("none", "old", "new") if kinds[k]], clauses)]
    rec.update(
        state="done",
        expected=expected,
        stored=len(stored),
        missing=len(missing),
        missingSeconds=missing,
        # Every missing second inside the stretch this recorder saw NO half in
        # charge has that as its reason; anything else is said as it is.
        missingWhy="; ".join(clauses),
        missingBy=kinds,
        perHalf={n: len(d.get("seconds") or []) for n, d in per.items()},
        # Which seconds each half holds, as [first, last] runs -- what the
        # page draws, one line per half.
        ranges={n: _runs_of(d.get("seconds") or []) for n, d in per.items()},
        historyAfterMs=(first_new - rec["at"]) if first_new else None,
        unanswered=errors,
        judgedAt=int(now * 1000),
    )
    rec.pop("waitingFor", None)
    _log(f"judged {rec['direction']}: {rec['stored']} of {expected} stored, "
         f"{rec['missing']} missing" + (f" ({', '.join(errors)} did not answer)" if errors else ""))
    return True


def _judge_loop() -> None:
    while True:
        try:
            with _lock:
                todo = [r for r in _changeovers if r.get("state") == "pending"]
            for rec in todo:
                done = _judge(rec)
                with _lock:
                    _save()
                if done:
                    continue
        except Exception as e:  # noqa: BLE001
            _log(f"judge error: {type(e).__name__}: {e}")
        time.sleep(5)


# --- the API -------------------------------------------------------------------

def request(kind: str) -> tuple:
    kind = str(kind or "").strip()
    if kind not in ("handover", "stop"):
        return 400, {"ok": False, "why": "kind is handover or stop"}
    _request.update(kind=kind, at=time.time())
    _log(f"request noted: {kind}")
    return 200, {"ok": True, "kind": kind, "at": int(_request["at"] * 1000)}


def clear() -> None:
    """Forget every changeover: a demo start begins with an empty card, so a
    customer never sees an earlier session's losses (undated) as their own."""
    with _lock:
        _changeovers.clear()
        _request.update(kind="", at=0.0)
        _save()
    _log("record cleared for a new demo")


def snapshot() -> dict:
    with _lock:
        halves = {n: {"answering": v["answering"], "active": v["active"],
                      "role": v["role"], "at": int(v["at"] * 1000)}
                  for n, v in _seen.items()}
        return {"holder": _track["holder"], "halves": halves,
                "window": {"before": WINDOW_BEFORE, "after": WINDOW_AFTER},
                "changeovers": [dict(r) for r in _changeovers]}


def start() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _load()
    for name, addr in HALVES:
        threading.Thread(target=_probe, args=(name, addr), name=f"redproof-{name}",
                         daemon=True).start()
    threading.Thread(target=_watch, name="redproof-watch", daemon=True).start()
    threading.Thread(target=_judge_loop, name="redproof-judge", daemon=True).start()
