"""Reset this gateway's own Perspective trial, in-process.

These gateways are unlicensed, so Perspective runs on a rolling 2-hour trial and
every session is replaced by a "Trial Expired" splash when it lapses.

`IgnitionGateway.getLicenseManager()` is declared to return LicenseManagerImpl,
which carries `resetTrial()`, so a gateway-scope script can call it directly:
no HTTP, no session cookie, no CSRF token, no browser.

8.3.9 RESETS ONLY A TRIAL THAT HAS EXPIRED. `TrialManager.resetTrial()` throws
`IllegalStateException: Trial has not expired yet.` otherwise, in-process and
through POST /data/api/v1/trial alike, so there is no resetting early. What
counts as expired is the TrialManager's own state, which only the expiry task
it arms at each reset (`demoExpiredFuture`, +7200 s) moves. Cancel that task and
the trial never expires inside, although GET /data/api/v1/trial reports
`expired: true` from the clock -- every reset is then refused until the gateway
restarts. Measured 24-28/09/2026 (docs/TRIALS.md). So nothing here touches it.

Lives in each Site because EAM carries a Site's own resources to its edge.
Nothing resets automatically: a person presses Reset on the console, or on the
Trials page wd-control serves while a gateway is expired (control/trialkeeper.py).
Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import Throwable as JThrowable

LOG = "Trial"


def _license_manager():
    from com.inductiveautomation.ignition.gateway import IgnitionGateway
    return IgnitionGateway.get().getLicenseManager()


def seconds_left():
    """Seconds of trial remaining on THIS gateway, or -1 if unavailable."""
    try:
        return int(_license_manager().getDemoTimeRemaining())
    except (Exception, JThrowable), e:
        system.util.getLogger(LOG).warn("cannot read trial: %s" % e)
        return -1


def reset_local():
    """Put this gateway's trial back to a full window. Returns minutes, or -1.

    -1 includes the ordinary refusal of a trial that has not expired yet.
    """
    log = system.util.getLogger(LOG)
    try:
        _license_manager().resetTrial()
    except (Exception, JThrowable), e:
        log.warn("trial reset refused: %s" % e)
        return -1
    left = seconds_left()
    log.info("trial reset: %d min remaining" % (left // 60))
    return left // 60

