# Trials: two hours, then a person resets it

These gateways are unlicensed, so Perspective runs on a rolling 2-hour trial;
when it lapses every session is replaced by a **Trial Expired** splash and a
good render looks exactly like a broken one. Nothing resets a trial
automatically. Someone presses Reset.

## How to reset

- **The Trials page**: https://console.test/_wd/trials, or `/_wd/trials` on any
  gateway's own `.test` name. It lists every gateway as minutes left, expired,
  not running or licensed, with a Reset button on an expired one. wd-control
  serves it, outside Ignition, so it works when a gateway's own pages are
  behind the trial-expired screen.
- **The console's EAM tab, Reset trials button.** Works while the hub console
  itself is usable.
- **`make trial-reset [GATEWAY=x]`** on the host. `make trial` shows minutes
  left on each gateway.

`make verify-demos` reports minutes left per gateway. An expired one is a
failure, and its fix line points at the Trials page.

## 8.3.9 resets only an expired trial

`TrialManager.resetTrial()` throws `IllegalStateException: Trial has not
expired yet.` unless the trial has expired, in process and through
`POST /data/api/v1/trial` alike. There is no resetting early, so every gateway
lapses every two hours and stays lapsed until someone resets it.

While lapsed, open sessions show the splash and MQTT Transmission stops its
clients, so Engine marks that edge offline and it re-births on the reset.

An expired Edge runs no gateway timer at all (measured 28/09/2026), so no
in-gateway script can reset it. That is why the Trials page lives in wd-control.

## Never cancel the expiry task

"Expired" to `resetTrial()` is the TrialManager's own state, which only the
one-shot expiry task armed at each reset (`demoExpiredFuture`, +7200 s)
changes. `GET /data/api/v1/trial` reports `expired: true` from the clock alone.
Cancel that task and the two disagree for good: the route says expired, every
reset is refused with "not expired yet", and only a gateway restart clears it.

Measured 24–28/09/2026: a script that cancelled the task before an early reset
left the hub and edge 3 expired for days with every reset refused. An edge
that left the task alone logged `Trial expired` at exactly +7200 s and took a
reset through the route with no restart. A gateway stuck in the disagreeing
state needs one restart.

## How a reset is made

From inside the gateway (a gateway-scope script, as the EAM tab button does
for the hub) there is no HTTP, session cookie or CSRF token to deal with:

```python
from com.inductiveautomation.ignition.gateway import IgnitionGateway
IgnitionGateway.get().getLicenseManager().resetTrial()
```

`GET /data/api/v1/trial` is an `OPEN_ROUTE`, so reading it needs no
credentials. `POST` needs `requirePermission(WRITE)`, so the Trials page signs
in with the gateway's own credential from `.gateways.env` and POSTs the reset.
It refuses while the trial still has time left; that is the route saying
there is nothing to reset, not a permissions failure.

## Warm standby

A standby's trial is its own and lapses on its own clock. A warm standby runs
no project scripts, and a request addressed to a redundant pair is one server
on the Gateway Network, so it comes straight back to whichever half is active.
Reset it on the Trials page like any other gateway.

## After any gap in uptime

A gateway that was down, or a VM that was suspended, comes back expired. Reset
it on the Trials page.
