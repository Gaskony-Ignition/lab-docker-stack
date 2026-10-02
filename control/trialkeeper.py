"""Every gateway's trial, and a Reset button a person presses.

8.3.9 resets only an expired trial (docs/TRIALS.md), so each gateway lapses
every two hours and waits for someone to reset it. An expired gateway's own
pages are behind the trial-expired screen, so the button cannot live there:
wd-control serves it at /trials (https://console.test/_wd/trials through the
proxy). A reset signs in with the gateway's credential from .gateways.env and
POSTs /data/api/v1/trial -- the same call `make trial-reset` makes.
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

import autologin

REPO = os.environ.get("REPO", "/work")
TIMEOUT = 5

# {stack: {"at": epoch, "result": str}} -- the last reset, shown on the page.
LAST: dict = {}


def _gateways() -> dict:
    """{stack: stanza} for every KIND=gateway manifest."""
    out = {}
    for path in sorted(glob.glob(os.path.join(REPO, "stacks", "*", "stack.meta"))):
        text = open(path).read().replace("\r", "")
        kind = re.search(r"^KIND=(.*)$", text, re.M)
        stanza = re.search(r"^STANZA=(.*)$", text, re.M)
        if kind and kind.group(1).strip() == "gateway" and stanza:
            out[os.path.basename(os.path.dirname(path))] = stanza.group(1).strip().strip('"')
    return out


def _trial(base: str) -> dict | None:
    """GET /data/api/v1/trial, or None if the gateway did not answer."""
    try:
        with urllib.request.urlopen(base + "/data/api/v1/trial", timeout=TIMEOUT) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001 -- stopped or starting
        return None


def _reset(base: str, stanza: str) -> str:
    """Sign in, then POST /data/api/v1/trial. Returns a short result."""
    cookies = autologin.login(base, *autologin.credential(stanza, os.path.join(REPO, ".gateways.env")))
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    # Writes need the session's CSRF token; 8.3 answers 403 without it.
    req = urllib.request.Request(base + "/data/app/session", headers={"Cookie": cookie})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        csrf = json.load(r).get("csrfToken", "")
    req = urllib.request.Request(base + "/data/api/v1/trial", data=b"", method="POST",
                                 headers={"Cookie": cookie, "X-CSRF-Token": csrf})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"


def reset(stack: str) -> str:
    """Reset one gateway's trial, on a person's request."""
    gws = _gateways()
    if stack not in gws:
        return "no such gateway"
    base = f"http://{stack}:8088"
    try:
        result = _reset(base, gws[stack])
        after = _trial(base) or {}
        result += ", still expired" if after.get("expired") else ", reset"
    except Exception as e:  # noqa: BLE001 -- reported on the page
        result = f"{type(e).__name__}: {e}"
    LAST[stack] = {"at": int(time.time()), "result": result}
    print(f"trial: {stack} reset on request -- {result}", flush=True)
    return result


def page() -> str:
    """The body of the Trials page: one row per gateway."""
    rows = []
    for stack in _gateways():
        d = _trial(f"http://{stack}:8088")
        # Every row has the button, disabled until a reset can work: a column
        # with a lone button in it read as broken.
        can = False
        if d is None:
            state = "not running"
        elif d.get("licenseMode") != "Trial":
            state = "licensed"
        elif d.get("expired"):
            state, can = "<strong>expired</strong>", True
        else:
            state = f"{int(d.get('trialSecondsLeft') or 0) // 60} min left"
        button = ("<form method=post action='trials/reset'>"
                  f"<input type=hidden name=gateway value='{html.escape(stack)}'>"
                  f"<button aria-label='Reset {html.escape(stack)}'"
                  + ("" if can else " disabled title='Available once the trial has expired'")
                  + ">Reset</button></form>")
        last = (LAST.get(stack) or {}).get("result", "")
        if last:
            state += f"<br><small>last reset: {html.escape(last)}</small>"
        rows.append(f"<tr><td>{html.escape(stack)}</td><td>{state}</td><td>{button}</td></tr>")
    return ("<p>Each gateway runs a two-hour trial. Ignition resets only one that "
            "has expired, so each Reset button comes on when that gateway lapses.</p>"
            "<table><thead><tr><th scope=col>Gateway</th><th scope=col>Trial</th>"
            "<th scope=col>Reset</th></tr></thead><tbody id=trials>"
            + "".join(rows) + "</tbody></table>"
            "<p><small>Updates every 5 s.</small> <a href=''>Refresh</a></p>"
            # Swaps the rows in place rather than reloading, so a click is never
            # lost to a reload; idle while the tab is hidden. One poll is six
            # reads of an open route, ~60 ms.
            "<script>setInterval(async()=>{if(document.hidden)return;try{"
            "const r=await fetch(location.href,{cache:'no-store'});if(!r.ok)return;"
            "const d=new DOMParser().parseFromString(await r.text(),'text/html');"
            "const n=d.getElementById('trials'),o=document.getElementById('trials');"
            "if(n&&o&&n.innerHTML!==o.innerHTML)o.innerHTML=n.innerHTML}catch(e){}},5000)"
            "</script>")
