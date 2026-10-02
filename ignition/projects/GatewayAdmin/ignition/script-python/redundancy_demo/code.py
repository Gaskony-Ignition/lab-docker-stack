"""Live state of the redundant pair, for the GatewayAdmin Redundancy page.

The page answers three questions a customer actually asks:

  1. Which half is doing the work right now, and which is standing by?
  2. Is the standby genuinely up to date, or is it quietly behind?
  3. What happens when the working half goes away?

Everything here reads the RedundancyManager IN PROCESS. A gateway-scope script
is already inside the JVM that owns the manager, so this needs no HTTP, no
session cookie, no CSRF token and no credentials -- the same reasoning as
gw_trial.reset_local(). The REST API under /data/api/v1/redundancy exists and is
what scripts/ign-redundancy.sh uses from OUTSIDE the gateway; from inside, going
out over HTTP just to come back in would mean putting a gateway password in a
project resource.

THE PAGE RUNS ON BOTH HALVES, AND THAT IS THE POINT.
GatewayAdmin is synchronised to the backup by redundancy itself, so the same
page is served by whichever half you point a browser at. Nothing here assumes it
is running on the master: every function starts from "what am I?" and describes
the peer relative to that. A page that only worked on the master would be
useless in the one moment the demonstration exists for -- when the master is
gone.

Jython 2.7: no f-strings, `except Exception, e:`, and `except Exception` does
NOT catch Java throwables, so anything touching Java names both.
"""
from java.lang import Throwable as JThrowable

LOG = "Redundancy"

# How long the hard-failover button waits before pulling the floor out. Long
# enough to read the message and look at the right screen, short enough that a
# customer does not think the button missed.
STOP_DELAY_SECONDS = 5

# The order a customer cares about, not an allow-list. Everything the manager
# reports is shown -- a table that quietly drops rows is worse than a long one,
# and "eleven subsystems kept in step" is a better answer than "three".
#
# In-process getMetrics() and the REST route's provider list are NOT the same
# set: the route reports every registered provider, while getMetrics() reports
# only those that have published a metric, which early in a gateway's life is a
# handful. Filtering this against a list written from the REST output emptied
# the table completely, with no error anywhere -- the rows simply were not there.
PREFERRED = [
    "projects",
    "config-resource",
    "tag-config-resource",
    "Tag - default - Runtime Values",
    "alarm-system",
    "alarm-shelf",
]

# How far back the event feed looks. Long enough to still show the last failover
# when someone opens the page to talk about it, short enough that the feed is
# not mostly startup noise.
EVENT_WINDOW_MINUTES = 30
EVENT_LIMIT = 12


def _logger():
    return system.util.getLogger(LOG)


def _gateway():
    from com.inductiveautomation.ignition.gateway import IgnitionGateway
    return IgnitionGateway.get()


def _manager():
    return _gateway().getRedundancyManager()


def _system_name():
    """This gateway's name, as the Gateway Network knows it.

    It is NOT `IgnitionGateway.getSystemName()` -- that method does not exist,
    and calling it raises inside a Perspective script transform, which renders
    the whole component as a red ERROR box and logs NOTHING. There is no stack
    trace to find: the binding fails, Perspective reports it in the browser, and
    the gateway log stays clean. That is why summary() below catches for itself.
    """
    try:
        return str(_gateway().getSystemPropertiesManager().getSystemName() or "")
    except (Exception, JThrowable), e:
        _logger().warn("could not read the system name: " + str(e))
        return ""


def _now():
    from java.lang import System as JSystem
    return JSystem.currentTimeMillis()


def _ago(millis):
    """'12s' / '4m' / '2h' for a timestamp, or None if there isn't one."""
    if not millis or millis <= 0:
        return None
    seconds = int((_now() - millis) / 1000)
    if seconds < 0:
        seconds = 0
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % (seconds / 60)
    return "%dh" % (seconds / 3600)


def _since(local_date_time):
    """Seconds since a java.time.LocalDateTime, or -1.

    getActiveSince() is a LocalDateTime with no zone, so it can only be compared
    against LocalDateTime.now() -- converting it to an instant needs a zone it
    does not carry, and guessing UTC puts the answer hours out on this stack,
    which runs in Australia/Adelaide.
    """
    if local_date_time is None:
        return -1
    try:
        from java.time import LocalDateTime
        from java.time.temporal import ChronoUnit
        return int(ChronoUnit.SECONDS.between(local_date_time, LocalDateTime.now()))
    except (Exception, JThrowable), e:
        _logger().debugf("could not age activeSince: %s", str(e))
        return -1


def _duration(seconds):
    if seconds is None or seconds < 0:
        return "--"
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % (seconds / 60)
    return "%dh" % (seconds / 3600)


# --- what am I, and what is on the other end ---------------------------------

def local():
    """This gateway's own redundancy state."""
    manager = _manager()
    state = manager.getCurrentState()
    return {
        "enabled": bool(manager.isRedundancyEnabled()),
        "role": str(state.getRole()),
        "activity": str(state.getActivityLevel()),
        "project": str(state.getProjState()),
        "history": str(state.getHistoryLevel()),
        "activeSeconds": _since(state.getActiveSince()),
        "isActive": bool(manager.isActive()),
        "isMaster": bool(manager.isMaster()),
        "address": str(manager.getLocalAddress() or ""),
        "name": _system_name(),
    }


def peer():
    """The other half, as this one currently understands it.

    Everything here comes from the peer's last status message, so on a half that
    has just lost its partner it describes the moment BEFORE the link dropped.
    `connected` is what says whether any of it is current, and the page must
    lead with that rather than with the stale values behind it -- a card reading
    "Active, Good" beside "not connected" is the exact shape of a status display
    that lies during the one event it exists to show.
    """
    manager = _manager()
    try:
        status = manager.getPeerConnectionStatus()
    except (Exception, JThrowable), e:
        _logger().warn("no peer status: " + str(e))
        return {"connected": False, "id": "", "activity": "Unknown",
                "project": "Unknown", "lastSync": None, "address": ""}

    if status is None:
        return {"connected": False, "id": "", "activity": "Unknown",
                "project": "Unknown", "lastSync": None, "address": ""}

    # The two are the other way round from what the names suggest, and this is
    # worth stating because the page reads wrong rather than breaking when they
    # are swapped: getPeerId() returns the peer's ADDRESS (172.x.x.x) and
    # getPeerAddress().toDescriptiveString() returns its NAME
    # (Ignition-Standard-Backup). The card carried an IP address as its title
    # for one deploy before this was noticed.
    name = ""
    try:
        server_id = manager.getPeerAddress()
        if server_id is not None:
            name = str(server_id.toDescriptiveString())
    except (Exception, JThrowable), e:
        _logger().debugf("no peer address: %s", str(e))

    return {
        "connected": bool(status.isConnected()),
        "id": name,
        "activity": str(status.getActivityLevel() or "Unknown"),
        "project": str(status.getProjectState() or "Unknown"),
        "lastSync": status.getLastSyncTimestamp(),
        "address": str(status.getPeerId() or ""),
    }


def providers():
    """Per-subsystem sync metrics: what redundancy is actually keeping in step.

    This is the answer to "is the standby really up to date". A customer will
    believe a green light for about four seconds; a table naming `projects`,
    `config` and how many updates are pending is what convinces them.
    """
    from com.inductiveautomation.ignition.gateway.redundancy import RedundancyManager

    try:
        metrics = _manager().getMetrics()
    except (Exception, JThrowable), e:
        _logger().warn("could not read redundancy metrics: " + str(e))
        return []

    rows = []
    for name in metrics.keySet():
        values = {}
        for metric in metrics.get(name):
            try:
                # getName() is a LocalizedString, NOT a String, and its
                # toString() renders the translated label -- so keying on
                # str(...) never matches RedundancyManager's METRIC_* constants,
                # which are the i18n KEYS. Every lookup silently returned the
                # default and the table showed raw provider ids and zeroes that
                # looked entirely plausible. getKey() is the one that matches.
                values[str(metric.getName().getKey())] = metric.getRawValue()
            except (Exception, JThrowable):
                continue

        pending = values.get(RedundancyManager.METRIC_PENDING_UPDATES, 0) or 0
        friendly = values.get(RedundancyManager.METRIC_PROVIDER_FRIENDLY_NAME) or str(name)
        error = values.get(RedundancyManager.METRIC_LAST_SYNC_ERROR) or ""
        pulled = values.get(RedundancyManager.METRIC_LAST_PULL_TIMESTAMP) or 0

        rows.append({
            "name": str(name),
            "label": str(friendly),
            "pending": int(pending),
            # Blank rather than "--" when there has been no pull: on the MASTER
            # there never is one (it is the side being pulled FROM), so a column
            # of dashes would read as eleven things not working.
            "lastPull": _ago(long(pulled)) or "",
            "error": str(error),
            # A word the view turns into a colour, so the palette stays in the
            # style packs rather than being hard-coded here.
            "level": "bad" if error else ("warn" if int(pending) > 0 else "good"),
        })

    def rank(row):
        try:
            return (PREFERRED.index(row["name"]), "")
        except ValueError:
            return (len(PREFERRED), row["label"])

    rows.sort(key=rank)
    return rows


def events(limit=EVENT_LIMIT):
    """The gateway's own redundancy log, newest first.

    This is what turns a failover from a colour change into something a customer
    believes: the gateway narrating its own state transitions, in its own words,
    as they happen. `eventsForSystems` is the same filter the gateway's status
    page uses -- the events carry the marker `evt:redundancy`, and asking for
    them by system rather than by logger name catches the half-dozen different
    loggers involved (BackupStateManager, MasterStateManager, R.Manager, ...).
    """
    from com.inductiveautomation.ignition.common.logging import LogQueryConfig

    try:
        # Ask for far more than we show. The dedupe below collapses each sync
        # from eleven lines to one, so a limit of 12 on the QUERY would leave a
        # single event on screen and look like a feed that had stopped.
        query = (LogQueryConfig.newBuilder()
                 .eventsForSystems("redundancy", "GatewayContextImpl.systemEvent")
                 .newerThan(_now() - EVENT_WINDOW_MINUTES * 60 * 1000)
                 .limitTo(400)
                 .build())
        results = _gateway().getLoggingManager().queryLogEvents(query)
    except (Exception, JThrowable), e:
        _logger().warn("could not read redundancy events: " + str(e))
        return []

    # The redundancy system logs the same sentence once per provider, so one
    # sync emits eleven lines and fills the feed with a single event. Collapse
    # them -- but NOT on the whole message: each copy ends with its own provider
    # name and sync id, so exact-match dedupe let all eleven through unchanged.
    # The key is the second it happened plus the opening of the sentence, which
    # collapses a burst while still letting the same sentence reappear later as
    # a genuinely new event.
    rows = []
    seen = set()
    for event in results.getEvents():
        try:
            level = str(event.getLevel())
            message = str(event.getMessage() or "")

            # The raw lines are prefixed with the provider they came from. Strip
            # it -- and dedupe on what is LEFT, not on the raw message: the
            # prefix is the one part that differs between the eleven copies, so
            # keying on the raw text let every one of them through.
            text = message.split(": ", 1)[-1] if ": " in message else message

            key = (int(event.getTimestamp() / 1000), text[:60])
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                # RAW MILLIS as well as the formatted time: the changeover is
                # measured by subtracting two of these, and "16:04:57" cannot be
                # subtracted from anything.
                "ts": int(event.getTimestamp()),
                "at": system.date.format(
                    system.date.fromMillis(event.getTimestamp()), "HH:mm:ss"),
                "ago": _ago(event.getTimestamp()) or "",
                "text": text,
                # The whole line: the prefix stripped above is sometimes the
                # part that matters ("...request initiated (force-failover).
                # Requested peer level: Active" leaves just "Active").
                "raw": message[:200],
                "level": {"ERROR": "bad", "WARN": "warn"}.get(level.upper(), "good"),
            })
        except (Exception, JThrowable):
            continue
        if len(rows) >= int(limit):
            break
    return rows


def pending_total():
    total = 0
    for row in providers():
        total = total + row["pending"]
    return total


# --- the cards ----------------------------------------------------------------

def _activity_words(activity, connected):
    """State text and severity for one half.

    'Warm' is the standby level this stack runs at, and 'WARM STANDBY' says
    something a customer understands where 'Warm' alone does not.
    """
    if not connected:
        return "OFFLINE", "bad"
    if activity == "Active":
        return "ACTIVE", "good"
    if activity == "Warm":
        return "WARM STANDBY", "good"
    if activity == "Cold":
        return "COLD STANDBY", "warn"
    return str(activity).upper(), "warn"


def _blank(message):
    """A shape the page can render when there is nothing real to show."""
    import demo_action
    blocked = demo_action.can(False, message)
    return {"paired": False, "banner": message, "bannerLevel": "warn",
            "cards": [], "providers": [], "canFailover": False,
            "canStop": False, "amActive": False, "amMaster": False,
            "myName": "", "peerName": "", "pending": 0, "text": message,
            "peerConnected": False, "notReady": "", "recoveryMode": "",
            # Never absent: an unbound sub-path draws a component's error box,
            # and these four are what decide whether the buttons are live.
            "can": {"failover": blocked, "stop": blocked, "resync": blocked,
                    "recovery": blocked}}


# The two halves by container name. Whichever of them is NOT serving this page
# is the standby, and it is the standby's trial that decides whether it can
# take over at all: a Warm standby runs no project scripts, and its trial is
# its own, so it lapses on its own two-hour clock.
HALF_TRIAL_URLS = {True: "http://ignition-backup:8088/data/api/v1/trial",
                   False: "http://ignition:8088/data/api/v1/trial"}
_PEER_TRIAL = {"at": 0, "value": None}
PEER_TRIAL_TTL_MILLIS = 10000

def peer_trial_minutes(am_master):
    """The standby's trial in minutes, 0 if expired, None if unreadable.

    GET /data/api/v1/trial is an OPEN_ROUTE, so no credential is involved -- the
    same route gateway_admin reads the edges' trials from. Memoised: the page
    polls every 2s and the answer moves by the minute.
    """
    now = _now()
    if (_PEER_TRIAL["value"] is not None
            and (now - _PEER_TRIAL["at"]) < PEER_TRIAL_TTL_MILLIS):
        return _PEER_TRIAL["value"][0]
    mins = None
    try:
        body = system.net.httpClient(timeout=3000).get(
            HALF_TRIAL_URLS[bool(am_master)]).json
        mins = 0 if body.get("expired") else \
            int(body.get("trialSecondsLeft", 0)) // 60
    except (Exception, JThrowable), e:
        _logger().debug("standby trial unreadable: " + str(e))
    _PEER_TRIAL["at"] = now
    _PEER_TRIAL["value"] = (mins,)
    return mins


def _not_ready():
    """Why the redundancy demo cannot be acted on yet, in the control plane's
    words. Lazily imported and never allowed to raise -- see
    gateway_admin._not_ready."""
    try:
        import demo_control
        return demo_control.ready_note("redundancy")
    except (Exception, JThrowable):
        return ""


def _stopped():
    """Is the redundancy DEMO stopped (the backup never started), as opposed to
    running and broken. The two look identical from inside the pair -- a master
    with no peer -- and must not be drawn the same way: one is "not started",
    the other is a fault. Never raises."""
    try:
        import demo_control
        return demo_control.demo_live("redundancy") == "stopped"
    except (Exception, JThrowable):
        return False


def _can(me, them, pending, not_ready):
    """Every button on the Redundancy tab, from the one snapshot the cards read.

    THE ONE THAT MATTERS IS `stop`. It was naked: `canStop` was computed as
    True whenever this gateway was paired at all -- including while the banner
    directly above it read PEER LOST -- and the view never read it anyway. With
    the backup down, that button restarts the gateway serving this very page
    with nothing to take over: the console vanishes for about a minute and no
    failover is demonstrated. The banner and the button disagreed, in the
    direction that takes the console down.

    So: a connected peer is required, and this half must be the one holding
    responsibility -- stop_active() falls through to stop_peer() otherwise,
    which is documented as unreachable on this stack.
    """
    import demo_action

    stop_label = "Restart this gateway"
    peer_name = them["id"] or "the peer"
    if not_ready:
        stop = demo_action.can(False, not_ready, stop_label, note="")
    elif not them["connected"]:
        # One clause. What the long version said -- nothing would take over,
        # and this page is served by the gateway being stopped -- is the
        # docstring above, and the reason the button exists guarded at all.
        stop = demo_action.can(
            False, "no link to %s, so nothing would take over" % peer_name,
            stop_label)
    elif not me["isActive"]:
        stop = demo_action.can(
            False, "this half is standing by -- use %s" % peer_name,
            stop_label)
    else:
        stop = demo_action.can(True, label=stop_label,
                               busy_label="Restarting this gateway...")

    # THE LABEL SAYS WHICH WAY IT GOES (see story()'s handoverLabel): pressed
    # while the backup holds responsibility it hands BACK, and a button that
    # names the opposite move invites the demonstrator to narrate it.
    backup_leads = bool(me["isActive"]) != bool(me["isMaster"])
    hand = "Hand back to master" if backup_leads else "Hand over to standby"
    if not_ready:
        failover = demo_action.can(False, not_ready, hand, note="")
    elif not them["connected"]:
        failover = demo_action.can(
            False, "no link to %s" % peer_name, hand)
    elif me["project"] != "Good":
        failover = demo_action.can(
            False, "still synchronising (%s)" % me["project"].lower(), hand)
    elif not me["isActive"]:
        failover = demo_action.can(
            False, "this half is standing by -- use %s" % peer_name, hand)
    else:
        failover = demo_action.can(True, label=hand,
                                   busy_label="Handing over...")

    # Sync is one-way, master -> backup. From the backup this is a request for
    # its OWN contents to be replaced, which resync()'s docstring has said
    # since it was written with no guard to match.
    if not_ready:
        resync = demo_action.can(False, not_ready, "Force full sync", note="")
    elif not them["connected"]:
        resync = demo_action.can(
            False, "no link to %s" % peer_name, "Force full sync")
    elif not me["isMaster"]:
        resync = demo_action.can(
            False, "sync runs master to backup -- use the master",
            "Force full sync")
    else:
        resync = demo_action.can(
            True, label="Force full sync", busy_label="Syncing...",
            note=("%d updates still queued" % pending) if pending else "")

    # applySettings on a pair mid-failover is the worst moment for it, and the
    # dropdown fired on every change with no state at all.
    if not_ready:
        recovery = demo_action.can(False, not_ready, note="")
    elif not them["connected"]:
        recovery = demo_action.can(False, "no link to %s" % peer_name)
    elif not me["isMaster"]:
        # The setting is the master's: written on the backup it changed only
        # the backup's own copy, which Ignition never reads (30/09/2026).
        recovery = demo_action.can(False, "set on the master -- hand back to change it")
    else:
        recovery = demo_action.can(True, busy_label="Saving...")

    return {"failover": failover, "stop": stop, "resync": resync,
            "recovery": recovery}


def summary():
    """Everything the page draws, and never an exception.

    A raised exception inside a Perspective script transform renders the bound
    component as a red ERROR box with NOTHING in the gateway log -- the failure
    is reported to the browser and nowhere a script can see it. That cost a
    deploy/scan/screenshot cycle to find a one-word method name. So this catches
    for itself: the page degrades to a legible message and the reason lands in
    the log where the next person will look.
    """
    try:
        return _summary()
    except (Exception, JThrowable), e:
        _logger().warn("redundancy summary failed: " + str(e))
        return _blank("Redundancy status unavailable: %s" % str(e))


def _summary():
    """Everything the page draws.

    One function rather than several so the two cards, the banner and the
    buttons can never disagree about which half is Active -- during a failover
    they are describing a system that is changing under them.
    """
    me = local()
    them = peer()
    rows = providers()
    not_ready = _not_ready()
    standby_trial = peer_trial_minutes(me["isMaster"]) if them["connected"] else None
    standby_expired = standby_trial == 0
    if standby_expired:
        # The control plane says the same thing ("trial is EXPIRED or
        # unreadable"); this says it with the fix, and the main panel says it
        # too -- see story().
        not_ready = ("The standby's trial has lapsed, so it cannot take over. Reset "
                     "it at console.test/_wd/trials.")
    pending = 0
    for row in rows:
        pending = pending + row["pending"]

    if not me["enabled"]:
        blank = _blank("Redundancy is not configured on this gateway")
        blank["providers"] = rows
        blank["text"] = "run: make redundancy"
        return blank

    my_state, my_level = _activity_words(me["activity"], True)
    their_state, their_level = _activity_words(them["activity"], them["connected"])

    # Order the cards Master then Backup regardless of which half is serving the
    # page, so the layout does not jump when responsibility moves. Which one is
    # Active is said in words on the card, not by its position.
    mine = {
        # isLocal/isActive are FLAGS because the card ORDER is not either of
        # those things -- see the ordering note below. Anything downstream that
        # wants "the half serving this page" or "the half in charge" must ask,
        # not count.
        "isLocal": True,
        "isActive": bool(me["isActive"]),
        "role": me["role"],
        "label": me["name"] or "This gateway",
        "sublabel": "%s - %s" % (me["role"], me["address"] or "this gateway"),
        "icon": "standard",
        "state": my_state,
        "stateLevel": my_level,
        "state2": "SERVING THIS PAGE",
        "state2Level": "good",
        "metrics": [
            {"value": _duration(me["activeSeconds"]),
             "name": "AT THIS LEVEL", "unit": "", "level": ""},
            {"value": me["project"].upper(), "name": "PROJECTS",
             "unit": "vs peer",
             "level": "good" if me["project"] == "Good" else "bad"},
            {"value": str(pending), "name": "PENDING", "unit": "updates",
             "level": "warn" if pending else ""},
        ],
        "signalText": "%s, %s" % (me["role"], my_state.lower()),
        "footer": "history %s" % me["history"].lower(),
    }

    theirs = {
        "isLocal": False,
        "isActive": str(them["activity"]).lower() == "active" and them["connected"],
        "role": "Backup" if me["isMaster"] else "Master",
        "label": them["id"] or "Peer gateway",
        "sublabel": "%s - %s" % (
            "Backup" if me["isMaster"] else "Master",
            them["address"] or "not connected"),
        "icon": "standard",
        "state": their_state,
        "stateLevel": their_level,
        "state2": "LAST SYNC %s" % (_ago(them["lastSync"]) or "never"),
        "state2Level": "good" if them["connected"] else "bad",
        "metrics": [
            {"value": _ago(them["lastSync"]) or "--", "name": "LAST SYNC",
             "unit": "ago" if them["lastSync"] else "never",
             "level": "" if them["connected"] else "bad"},
            {"value": (them["project"] or "?").upper(), "name": "PROJECTS",
             "unit": "on peer",
             "level": "good" if them["project"] == "Good" else "bad"},
            {"value": "YES" if them["connected"] else "NO", "name": "LINK",
             "unit": "redundancy", "level": "" if them["connected"] else "bad"},
        ],
        "signalText": them["connected"] and "linked" or "no link to the peer",
        "footer": them["address"] or "peer address unknown",
    }

    # MASTER FIRST, BACKUP SECOND -- never local-first. The order is fixed so the
    # two cards do not swap places the moment responsibility moves, which would
    # make a working failover look like the page had lost track of both halves.
    #
    # The cost is that cards[0] is the LOCAL half only when this is the master,
    # and reading it as "me" is therefore correct on one gateway and inverted on
    # the other -- inverted, specifically, once a handover has happened, which is
    # the one moment anybody is looking. `story()` did exactly that and named the
    # warm master as the half serving the screen (measured through the front door
    # on 31/08/2026). Read `isLocal` / `isActive` instead; never the index.
    cards = [mine, theirs] if me["isMaster"] else [theirs, mine]

    if not them["connected"]:
        banner, level = "PEER LOST - this gateway is running alone", "bad"
    elif me["project"] != "Good":
        banner, level = "SYNCHRONISING - %s" % me["project"], "warn"
    elif me["isActive"]:
        banner, level = "%s IS ACTIVE - peer is %s" % (
            me["role"].upper(), them["activity"].lower()), "good"
    else:
        banner, level = "%s IS ACTIVE - this half is %s" % (
            (them["id"] or "the peer").upper(), me["activity"].lower()), "good"

    return {
        "paired": True,
        "banner": banner,
        "bannerLevel": level,
        "cards": cards,
        "providers": rows,
        # Handing over needs a peer to hand over TO. Offering the button while
        # the link is down would produce a request that goes nowhere and a page
        # that then has to explain why nothing happened.
        "canFailover": them["connected"] and me["project"] == "Good",
        # TIGHTENED, AND NOW ACTUALLY READ BY THE VIEW. It used to be the
        # constant True beside a banner that could say PEER LOST -- see _can.
        "canStop": bool(them["connected"]) and bool(me["isActive"]),
        "can": _can(me, them, pending, not_ready),
        # What the MASTER is set to, read from the gateway every poll. The
        # dropdown showed a browser-session value, which went back to
        # Automatic when the page moved to the backup (30/09/2026).
        "recoveryMode": master_recovery(me),
        "notReady": not_ready,
        "standbyTrial": standby_trial,
        "standbyExpired": standby_expired,
        "amActive": me["isActive"],
        "amMaster": me["isMaster"],
        "myName": me["name"],
        "peerName": them["id"],
        # `peerName` is the LAST KNOWN name and survives the peer going away, so
        # it cannot answer "is the link up right now". These three can, and the
        # changeover is measured from them.
        "peerConnected": bool(them["connected"]),
        "peerLastSync": them["lastSync"],
        "activeSeconds": me["activeSeconds"],
        "pending": pending,
        "text": banner,
    }


def card(index):
    """One card's parameters, by position. 0 is always the master's half."""
    cards = summary().get("cards") or []
    try:
        return cards[int(index)]
    except (IndexError, ValueError):
        return {"label": "--", "sublabel": "", "icon": "standard",
                "state": "NOT PAIRED", "stateLevel": "warn", "state2": "",
                "state2Level": "warn", "metrics": [], "signalText": "",
                "footer": "run: make redundancy"}


# --- what the OTHER pages draw ------------------------------------------------
#
# The EAM and Store & Forward pages show the hub, and the hub is half of a
# redundant pair. Leaving the backup off those diagrams made them quietly wrong:
# a failover moves the gateway doing all the work on both of those pages, and
# neither of them showed it happening. So both draw the pair, using these.
#
# Nothing here raises -- summary() already catches for itself, because a script
# transform that throws renders a red ERROR box and logs NOTHING.

def pair_state():
    """The pair, reduced to what a second diagram needs to draw it."""
    d = summary()
    cards = d.get("cards") or []
    if not d.get("paired") or len(cards) < 2:
        return {"paired": False, "label": "NO BACKUP", "detail": "not paired",
                "footer": "run: make redundancy", "synced": False}
    if _stopped():
        # Not "PEER LOST - this gateway is running alone" -- the same misreading
        # as the Redundancy tab's headline, on the tabs that draw the pair.
        return {"paired": True, "label": "BACKUP", "detail": "not started",
                "footer": "", "synced": False}
    them = cards[1]
    # The chip is 186px wide and sits BETWEEN two cards that are already
    # labelled, so "REDUNDANT PAIR" over the full banner sentence spent both
    # lines saying nothing and then clipped the half that mattered
    # ("MASTER IS ACTIVE - peer is" -- the word "warm" fell off the end).
    # Split the banner across the two lines it already has instead: which half
    # is active on top, what the other one is doing underneath. Same facts, and
    # each line is short enough to render whole.
    banner = d.get("banner", "")
    if " - " in banner:
        head, tail = banner.split(" - ", 1)
    else:
        head, tail = banner, ""
    return {
        "paired": True,
        # Which half is doing the work -- the thing a failover changes and the
        # reason these cards are here at all.
        "label": head or "REDUNDANT PAIR",
        "detail": tail,
        # No footer. It repeated the backup card standing right beside it, and
        # the extra width pushed the chip past its container -- which Perspective
        # answers with a horizontal SCROLLBAR on hover, not by growing the box.
        "footer": "",
        # Sync only counts as live when the peer is actually connected; a stale
        # last-known status must not animate as though data were moving.
        "synced": bool(d.get("peerName")) and them.get("stateLevel") == "good",
    }


def pair_classes():
    """Style classes for the horizontal link between the two halves."""
    p = pair_state()
    return "wd-conn-h wd-conn-h--%s" % ("sync" if p["synced"] else "stalled")


def pair_chip_classes():
    p = pair_state()
    return "wd-conn-chip wd-conn-chip--%s" % ("sync" if p["synced"] else "stalled")


def pair_text(which):
    return pair_state().get(which, "")


def backup_card():
    """The backup half, shaped as GatewayCard parameters."""
    c = card(1)
    # 'standard' rather than 'edge': it is a full gateway, and drawing it with
    # the edge artwork would say the opposite of what the demo is about.
    c = dict(c)
    c["icon"] = "standard"
    # Same information as this card's own LAST SYNC tile, and on these two
    # pages the badge row is what was clipping the gateway's name.
    c["state2"] = ""
    # No footer either: it repeated the peer address already printed in the
    # sublabel two lines above it, and cost a row of card height to do it.
    c["footer"] = ""
    if _stopped():
        # OFFLINE in red over "67h LAST SYNC" and "NO LINK", both red, was a
        # stopped demo drawn as a failed one -- on the EAM tab, which is not
        # even about redundancy.
        dash = u"\u2014"
        c["state"], c["stateLevel"] = "NOT STARTED", "neutral"
        c["metrics"] = [dict(m, value=dash, unit="", level="")
                        for m in (c.get("metrics") or [])]
    return c


# --- the demonstration --------------------------------------------------------

def note_request(kind):
    """Tell wd-control's changeover recorder a handover or stop was asked for,
    so a planned one is timed from the click. Never raises: the recorder
    still sees the changeover without it, only without its start."""
    try:
        system.net.httpClient(timeout=2000).post(
            "http://wd-control:8080/redundancy/request",
            data=system.util.jsonEncode({"kind": kind}),
            headers={"Content-Type": "application/json"})
    except (Exception, JThrowable), e:
        _logger().debug("could not note the request: %s" % e)


def failover():
    """Hand responsibility to the peer -- the planned, reversible half of the demo.

    requestPeerActivityLevel(Active) is a REQUEST to the peer, so it must be
    issued from the half that currently holds responsibility. Calling it on a
    warm standby is accepted and does nothing visible, which is exactly the kind
    of silent no-op that reads as a broken button -- hence the guard.

    Takes about ten seconds to settle. Nothing restarts, no session drops, and
    running it a second time hands responsibility straight back.
    """
    me = local()
    them = peer()

    if not them["connected"]:
        return "No link to the peer -- there is nothing to fail over to."
    if not me["isActive"]:
        return ("This half is already standing by. Run the failover from %s, "
                "which is holding responsibility." % (them["id"] or "the active half"))

    from com.inductiveautomation.ignition.gateway.redundancy.types import ActivityLevel
    note_request("handover")
    try:
        _manager().requestPeerActivityLevel(ActivityLevel.Active)
    except (Exception, JThrowable), e:
        _logger().warn("failover request failed: " + str(e))
        return "The failover request was refused: %s" % str(e)

    _logger().info("failover requested -- asked %s to become Active" % (them["id"] or "peer"))
    return "Handing responsibility to %s. Give it about ten seconds." % (
        them["id"] or "the peer")


def resync():
    """Force a full configuration sync, master -> backup.

    Sync is one-way by design, so this is only meaningful from the master; from
    the backup it is a request for its own contents to be replaced -- which is
    what this docstring said for months with nothing enforcing it.
    """
    me = local()
    them = peer()
    if not them["connected"]:
        return "Refused: there is no link to %s, so there is nothing to sync to." % (
            them["id"] or "the peer")
    if not me["isMaster"]:
        return ("Refused: sync runs master to backup. From this half it would "
                "ask for this gateway's own contents to be replaced.")
    try:
        _manager().forceConfigurationSync()
    except (Exception, JThrowable), e:
        _logger().warn("resync failed: " + str(e))
        return "The resync was refused: %s" % str(e)
    _logger().info("full configuration sync forced")
    return "Full configuration sync started."


MASTER_URL = "http://ignition:8088"
PROOF_ROUTE = "/system/webdev/GatewayAdmin/redundancy/proof"


def _recovery_local():
    try:
        return str(_manager().getSettings().getMasterRecoveryMode())
    except (Exception, JThrowable):
        return ""


def master_recovery(me):
    """The master's recovery mode, or "" when it cannot be read. On the
    backup, asked of the master over the proof route, which answers on a
    standby; a master that is down has no setting to show."""
    if me.get("isMaster"):
        return _recovery_local()
    try:
        r = system.net.httpClient(timeout=1500).get(
            MASTER_URL + PROOF_ROUTE, params={"only": "recovery"})
        j = r.json if r.good else {}
        return str((j or {}).get("recovery") or "")
    except (Exception, JThrowable):
        return ""


def set_recovery(mode):
    """Automatic or Manual master recovery.

    The difference is worth showing: with Automatic the master takes its
    responsibility back the moment it returns, which is what most sites want and
    what makes the hard-failover demo self-healing. With Manual it comes back as
    a standby and waits for a human, which is what sites with expensive
    start-up sequences choose so that a flapping master cannot thrash the plant.
    """
    from com.inductiveautomation.ignition.gateway.redundancy.types import RecoveryMode

    wanted = str(mode).strip().capitalize()
    if wanted not in ("Automatic", "Manual"):
        return "Recovery mode must be Automatic or Manual, not %r." % mode

    manager = _manager()
    try:
        settings = manager.getSettings()
        if str(settings.getMasterRecoveryMode()) == wanted:
            return "Master recovery is already %s." % wanted
        settings.setMasterRecoveryMode(RecoveryMode.valueOf(wanted))
        manager.applySettings(settings)
    except (Exception, JThrowable), e:
        _logger().warn("could not set recovery mode: " + str(e))
        return "Could not change the recovery mode: %s" % str(e)

    _logger().info("master recovery mode set to %s" % wanted)
    return "Master recovery is now %s." % wanted


def stop_local(delay=STOP_DELAY_SECONDS):
    """Restart THIS gateway -- the hard half of the demonstration.

    IgnitionGateway.restart() takes the gateway genuinely down: Perspective
    sessions on it drop, the redundancy link drops, and the peer stops hearing
    from it. That is the event worth showing, and unlike stopping the container
    it needs nothing on the host and cannot be left half-done -- the gateway
    brings itself back in about a minute and, with recovery set to Automatic,
    takes its responsibility back.
    """
    from java.util.concurrent import TimeUnit

    seconds = int(delay or 0)

    # Delayed, and not merely for the drama: this is called from a Perspective
    # script action, and restarting inside the handler kills the web server
    # while it is still writing the response. The click then reports a failure
    # for something that worked perfectly -- the same trap that made edge_link
    # arm its cut rather than perform it. Same fix: return first, act after.
    def _go():
        _logger().warn("restarting this gateway for the redundancy demonstration")
        try:
            _gateway().restart()
        except (Exception, JThrowable), e:
            _logger().error("restart failed: " + str(e))

    note_request("stop")
    try:
        _gateway().getExecutionManager().executeOnce(_go, seconds, TimeUnit.SECONDS)
    except (Exception, JThrowable), e:
        _logger().warn("could not schedule the restart: " + str(e))
        return "Could not schedule the restart: %s" % str(e)

    return ("Restarting this gateway in %ds. It is back in about a minute; "
            "watch the peer take over." % seconds)


def stop_peer():
    """Ask the OTHER half to go down, so this session survives to watch it.

    UNREACHABLE FROM THIS PAGE ON THIS STACK, and worth keeping anyway.

    The idea was that you would open the page on the standby and watch the
    active half disappear without losing the browser. Perspective does not allow
    it: a client connecting to a STANDBY is redirected to the active node, which
    it reaches at the active node's *public address* -- a different origin from
    the standby's, so the browser blocks the call:

        Access to XMLHttpRequest at 'http://ignition.test/data/perspective/
        redundancy/status' from origin 'http://localhost:8388' has been blocked
        by CORS policy

    and the session hangs at "Connecting" forever. Verified 05/08/2026 with a
    45-second wait. So a standby serves no Perspective session at all, and the
    only half that can show this page is the active one -- which means
    stop_active() below always resolves to stop_local().

    It is kept because it is correct for the deployment this stack imitates: put
    both halves behind one load balancer, as production redundancy is, and the
    client's origin matches the active node's address, the redirect works, and
    this function is how the surviving session takes the other half down.
    """
    them = peer()
    if not them["connected"]:
        return "No link to the peer -- ask it to stop from its own page."

    target = them["id"]
    if not target:
        return "The peer has not identified itself yet; try again in a moment."

    # `remoteServer` is the GATEWAY NETWORK name, and it is not necessarily what
    # getPeerAddress() spells. sf_demo hit the same thing with the edges, where
    # the Gateway Network name and the EAM agent name differ only by case
    # ("ignition-edge1" against "Ignition-Edge1") -- a difference that fails as
    # "server not found" and reads exactly like the peer being offline.
    #
    # Rather than guess once, try the forms the pair is known to use and say
    # which one worked, so the next person does not have to rediscover it.
    candidates = []
    for form in (target, target.lower(), target.replace("-Backup", "").replace("-Master", "")):
        if form and form not in candidates:
            candidates.append(form)

    last = ""
    for server in candidates:
        try:
            system.util.sendRequest(
                "GatewayAdmin", "redundancyStop",
                {"seconds": STOP_DELAY_SECONDS},
                remoteServer=server, timeoutSec=15)
        except (Exception, JThrowable), e:
            last = str(e)
            _logger().debugf("redundancyStop to '%s' failed: %s", server, last)
            continue

        _logger().info("asked '%s' to restart for the redundancy demonstration" % server)
        return ("%s is stopping. It restarts itself in about a minute -- watch "
                "this half take over." % target)

    _logger().warn("could not reach the peer to stop it: " + last)
    return ("Could not reach %s over the Gateway Network (%s). Use its own page, "
            "or run: make redundancy-fail" % (target, last))


def stop_active():
    """Stop whichever half currently holds responsibility.

    In practice this is always stop_local(), because only the active half can
    serve this page -- see stop_peer() for why. The branch is kept so the page
    behaves correctly if these gateways are ever put behind one load balancer,
    which is what makes a standby session possible.

    GUARDS ITSELF as well as being guarded by `can.stop`: the button is not the
    only caller, and this is the one action on the console that can take the
    console away. The redundancyStop message handler deliberately does NOT go
    through here -- it is the peer asking over the Gateway Network, which
    proves a peer by construction.
    """
    them = peer()
    if not them["connected"]:
        return ("Refused: there is no link to %s, so nothing would take over. "
                "This page is served by the gateway you just asked to stop."
                % (them["id"] or "the peer"))
    me = local()
    if me["isActive"]:
        return stop_local()
    return stop_peer()


# --- the road in, and why it decides whether this page survives ---------------
#
# THE DEMONSTRATION HAS A DOOR THAT WORKS AND A DOOR THAT DOES NOT, and until
# 28/08/2026 the page said nothing about either. A Warm standby serves NO
# Perspective session at all, so a browser pinned to one gateway loses this page
# the instant that gateway hands over -- holding the very button needed to hand
# back. Measured in the room: the presenter was locked out of their own demo.
#
#   https://ignition.test  -> npm -> HAProxy -> whichever half reports Active
#   https://console.test   -> the HUB, directly. Dies on failover.
#   http://localhost:29088 -> the HUB, directly. Dies on failover.
#
# Perspective does not hand a page its own origin -- there is no session prop
# for the request host, and the schemas in perspective-common carry nothing of
# the kind. So this does NOT guess. It states the rule and names the one URL
# that holds, which is honest and needs no detection. Claiming to know the
# origin and being wrong would be worse than saying nothing.
FRONT_DOOR = "https://ignition.test/data/perspective/client/GatewayAdmin"

# The halves' own doors, for reaching one deliberately. Neither survives a
# handover, and that is the point of showing them next to the front door.
DIRECT_DOORS = [
    ("console.test", "the hub, pinned"),
    ("backup.test", "the backup, pinned"),
]


def front_door():
    """What to put in front of the failover buttons.

    Returned rather than hard-coded in the view so the URL lives in one place;
    the view binds this and the buttons' confirm text reads from the same dict.
    """
    return {
        "url": FRONT_DOOR,
        "warning": ("This page survives a handover ONLY on ignition.test. "
                    "On console.test or a host port you are pinned to one "
                    "gateway, and failing over ends this session."),
        "short": "open the front door",
    }


def _request_path():
    """The road from the browser to whichever half is serving -- as a strip.

    Four hops, because every one is a thing a customer asks about and three of
    them are invisible on the current page. Only the Active half takes traffic;
    the other is drawn and dark.

    Reads the card keys that _summary() actually produces -- `label` and
    `state` -- NOT a `title`/`role` pair, which do not exist on these dicts. The
    first cut of this function invented both, and every gateway would have
    rendered as the fallback string with nothing in any log to say why.
    """
    d = summary()
    cards = d.get("cards") or []
    active_name = ""
    standby_name = ""
    for c in cards:
        name = c.get("label") or ""
        if (c.get("state") or "").strip().lower() == "active":
            if not active_name:
                active_name = name
        elif not standby_name:
            standby_name = name
    return {
        "hops": [
            {"label": "BROWSER", "detail": "your session"},
            {"label": "npm", "detail": "ignition.test"},
            {"label": "HAProxy", "detail": "picks the Active half"},
        ],
        "active": active_name or (d.get("myName") or "MASTER"),
        "standby": standby_name or (d.get("peerName") or "BACKUP"),
        "paired": bool(d.get("paired")),
    }


def _health_check():
    """The one string the whole failover turns on, shown rather than described.

    HAProxy does not ask "is it up" -- both halves are up and both answer 200.
    It matches the ROLE in the body of /system/gwinfo, which is what makes a
    PLANNED handover work: a master that has handed over is still perfectly
    alive and must stop receiving traffic anyway. A liveness check would keep
    sending it there, with nothing to notice. It is the entire mechanism and it
    is otherwise invisible on this page.
    """
    d = summary()
    rows = []
    for c in (d.get("cards") or []):
        state = (c.get("state") or "").strip()
        rows.append({
            "gateway": c.get("label") or "?",
            "reports": "RedundantNodeActiveStatus=" + (state or "?"),
            "serving": state.lower() == "active",
        })
    return {"route": "/system/gwinfo", "rows": rows}


def _sync_facts():
    """What redundancy carries, and the three things it does NOT.

    The second list is the useful half and is never on screen anywhere. Each
    entry cost this project real debugging time:

      publicAddress  per-gateway, NOT synchronised. The backup was still
                     auto-detecting its own container address long after the
                     master had been set -- and that setting is exactly what
                     makes the post-failover redirect land on the same origin.
      host ports     compose, not gateway state; the pair share a name but not
                     a port map.
      trial clock    licensing is per gateway, and the master cannot reach the
                     standby's either, because a redundant pair is ONE server
                     on the Gateway Network. Each half is reset on its own.
    """
    rows = providers()
    carried = []
    for r in rows:
        carried.append({
            "name": r.get("label") or r.get("name"),
            "pending": r.get("pending", 0),
        })
    return {
        "carried": carried,
        "pending_total": pending_total(),
        "not_carried": [
            {"name": "publicAddress",
             "why": "per-gateway -- and it is what makes the redirect same-origin"},
            {"name": "host ports",
             "why": "compose, not gateway state"},
            {"name": "the Perspective trial",
             "why": "licensing is per gateway -- each half is reset on its own"},
        ],
    }


# --- guarded entry points -----------------------------------------------------
#
# Every one of the three above is reached from a Perspective transform, and a
# transform that RAISES renders the component as a red ERROR box and logs
# NOTHING anywhere a script can see -- the failure goes to the browser and stops
# there. summary() already catches for itself for exactly this reason; these do
# the same rather than trusting that the dicts they read never change shape
# again. They read card keys, and the first draft of them read two keys that do
# not exist.

def request_path():
    """See _request_path(). Never raises."""
    try:
        return _request_path()
    except (Exception, JThrowable), e:
        _logger().warn("request_path failed: %s" % e)
        return {"hops": [], "active": "?", "standby": "?", "paired": False}


def health_check():
    """See _health_check(). Never raises."""
    try:
        return _health_check()
    except (Exception, JThrowable), e:
        _logger().warn("health_check failed: %s" % e)
        return {"route": "/system/gwinfo", "rows": []}


def sync_facts():
    """See _sync_facts(). Never raises."""
    try:
        return _sync_facts()
    except (Exception, JThrowable), e:
        _logger().warn("sync_facts failed: %s" % e)
        return {"carried": [], "pending_total": 0, "not_carried": []}


# --- how fast, and what survives ---------------------------------------------
#
# The two questions a customer asks straight after "which half is serving me":
# how quickly does the spare take over, and does anything get lost while it
# does. Both are answered from what the gateway already knows -- nothing here
# is timed by this page or estimated.

def _data_dir():
    """The gateway's data/ directory, or None.

    Tried in order rather than assumed: a wrong path here would raise inside a
    binding transform, and a transform that raises renders a red ERROR box with
    nothing in the log.
    """
    try:
        return str(_gateway().getSystemManager().getDataDir().getAbsolutePath())
    except (Exception, JThrowable):
        pass
    for candidate in ("data", "/usr/local/bin/ignition/data"):
        try:
            from java.io import File
            if File(candidate, "redundancy.xml").exists():
                return candidate
        except (Exception, JThrowable):
            continue
    return None


def _configured():
    """The changeover budget, as CONFIGURED -- not as guessed.

    `data/redundancy.xml` is a plain Java properties file the gateway reads at
    startup and on save, and it carries the two numbers that decide how long an
    unplanned loss takes to notice:

        redundancy.gan.pingRate       how often the halves ping    (ms)
        redundancy.gan.pingMaxMissed  how many may be missed first

    So the standby waits pingRate x pingMaxMissed before concluding the master
    is gone. That product is the number to put in front of a customer -- it is
    the promise the configuration makes, against which the measured figure
    beside it can be read.
    """
    out = {"pingRate": 0, "pingMaxMissed": 0, "seconds": 0,
           "standby": "", "recovery": ""}
    d = _data_dir()
    if not d:
        return out
    try:
        text = system.file.readFileAsString(d + "/redundancy.xml")
    except (Exception, JThrowable), e:
        _logger().debugf("could not read redundancy.xml: %s", str(e))
        return out

    def entry(key):
        needle = '<entry key="%s">' % key
        i = text.find(needle)
        if i < 0:
            return ""
        j = text.find("</entry>", i)
        return text[i + len(needle):j].strip() if j > i else ""

    try:
        out["pingRate"] = int(entry("redundancy.gan.pingRate") or 0)
        out["pingMaxMissed"] = int(entry("redundancy.gan.pingMaxMissed") or 0)
    except (Exception, JThrowable):
        pass
    out["standby"] = entry("redundancy.standbyactivitylevel")
    out["recovery"] = entry("redundancy.masterrecoverymode")
    out["seconds"] = int((out["pingRate"] * out["pingMaxMissed"]) / 1000)
    return out


def _measured_outage(d):
    """How long the last UNPLANNED changeover actually took.

    Measured from two timestamps the gateway keeps, not from its log: the log
    was the obvious source and it does not work. Killing the master outright and
    catching the survivor mid-outage, the backup's redundancy event store held
    exactly ONE line -- "Took charge" -- and nothing at all about losing the
    peer, so there was no earlier event to subtract and every real failure was
    being reported as "planned".

    What it does keep is when it last heard from the peer, and when it took
    charge:

        peer.lastSync ------------------------> activeSince
        the last contact                        this half took over

    The gap is the outage: detection plus activation, which is the number the
    configured `pingRate x pingMaxMissed` promises. It is only meaningful while
    the peer is still gone -- once it returns, lastSync moves and the subtraction
    is of two unrelated moments -- so it is offered only then, and the figure
    reverts to "when" once the pair is whole again.
    """
    if d.get("peerConnected"):
        return None
    last_sync = d.get("peerLastSync")
    active_secs = d.get("activeSeconds")
    if not last_sync or active_secs is None or active_secs < 0:
        return None
    took_at = _now() - (active_secs * 1000)
    gap = int((took_at - last_sync) / 1000)
    # A negative gap means it took charge before its last recorded contact,
    # which is not a measurement of anything; a huge one means these two
    # timestamps belong to different events.
    if gap < 0 or gap > 600:
        return None
    return gap


def _last_takeover(evts):
    """When this half last took charge, in words ('51s'), or ''.

    The DURATION is not in the log -- see _measured_outage. This answers only
    "when", which the log does carry reliably.
    """
    for e in evts:                      # newest first
        if _plain(e.get("text", "")) == "Took charge":
            return _ago(e.get("ts")) or ""
    return ""


# --- the CUSTOMER's view of all this -------------------------------------------
#
# Everything above answers an engineer's questions: which providers are in step,
# what the pending count is, what the gateway logged. A customer standing in
# front of the screen is asking three much smaller ones -- who is serving me,
# is there a spare, and what happens when this breaks -- and the page was
# answering them in the middle of a page of prose, raw /system/gwinfo output and
# eleven sync providers. It could not be shown to a customer without a talk
# track, which is the same as saying it did not work.
#
# So this returns the story, already told: one headline, two named halves, and a
# short plain-English history. No raw log lines, no provider table, no jargon
# the room has to be taught first.

# Ordered, first match wins. The gateway narrates state transitions in its own
# vocabulary ("Role=Master, Activity level=Active, Project state=Good"), which is
# precise and unreadable across a room. Anything that does not match falls
# through to a trimmed version of the real line rather than to an invented one --
# a demo that makes up its own history is worse than one that shows a raw string.
_STORY_RULES = [
    # The bare word first: some transitions log just "Active" or "Warm", and the
    # fallback trimmed those to a single word that says nothing to a room.
    #
    # NOT "this gateway": the phrase has no referent across a room, and it means
    # a DIFFERENT machine before and after a handover, so a feed carrying both
    # reads as a contradiction. The heading names the half these lines came from
    # once; the lines themselves stay about the event.
    ("activity level=active",      "Took charge"),
    ("activity level=warm",        "Went on standby"),
    ("activity level=cold",        "Standing by"),
    ("activity level=undecided",   "Deciding which half leads"),
    ("contextstate = running",     "Gateway running"),
    ("contextstate = starting",    "Gateway starting up"),
    ("contextstate = stopping",    "Gateway stopping"),
    ("state=stopped",              "Gateway stopped"),
    ("synchroniz",                 "Configuration copied to the standby"),
    ("synchronis",                 "Configuration copied to the standby"),
    ("connected",                  "Link to the other half established"),
    ("disconnect",                 "Link to the other half lost"),
    ("failover",                   "Responsibility handed over"),
    ("has no data",                "Standby has nothing to send back yet"),
    ("active",                     "Took charge"),
    ("warm",                       "Went on standby"),
    ("history level",              "Checking the two halves match"),
]


# --- the proof: how fast, and was anything lost ------------------------------
#
# One tag on the pair itself, one value a second, historised to the Postgres
# SQL historian both halves write to. The value IS the second it was taken in
# (whole seconds since the epoch), so either half computes the same number for
# the same second and "missing" means one thing: a second with no stored row.
# wd-control (control/redproof.py) records each changeover from OUTSIDE the
# pair -- a handover drops the page's own session -- and asks both halves
# through the WebDev route `redundancy/proof` which seconds are stored.
#
# NOT a Core Historian (tried 22/09/2026, docs/REDUNDANCY.md): on 8.3.8 a
# restart whose startup cache load fails registers the tag again under a new
# node, and a query by path then finds only the newest node's rows -- the
# history before it is on disk but reads as lost.

PROOF_HISTORIAN = "Postgres"
PROOF_FOLDER = "Redundancy"
PROOF_TAG = "Clock"
PROOF_PATH = "[default]%s/%s" % (PROOF_FOLDER, PROOF_TAG)
PROOF_EXPRESSION = "floor(toMillis(now(0)) / 1000)"
# Twice a second, so every second is seen even when an evaluation lands a few
# milliseconds either side of a boundary; the value changes once a second and
# history stores on change, so the store still gets one row per second.
PROOF_RATE_MS = 500
PROOF_MAX_WINDOW_S = 900


def _proof_def():
    return {"name": PROOF_TAG, "tagType": "AtomicTag", "valueSource": "expr",
            "expression": PROOF_EXPRESSION, "dataType": "Int8",
            "executionMode": "FixedRate", "executionRate": PROOF_RATE_MS,
            "historyEnabled": True, "historyProvider": PROOF_HISTORIAN,
            "sampleMode": "OnChange", "historicalDeadbandStyle": "Discrete",
            "historicalDeadband": 0}


def ensure_proof():
    """Create or repair the proof tag. Idempotent; writes only on a difference.
    Returns what it did, in words. Run by the RedundancyProof timer, which runs
    on whichever half is active; tag config reaches the other half by sync.

    History is on only while the Redundancy demo runs. Stopped, its Postgres
    is down too, and a row a second piles up in the hub's store-and-forward
    buffer, which the engine rescans every 100 ms (measured 22/09/2026: a
    22,000-batch backlog cost the hub ~70% of a core).

    "Runs" means every one of its stacks is up. "partial" -- which is what a
    stopped Redundancy demo reads while the MQTT demo keeps the shared Postgres
    up -- is off: the proof would otherwise record a second a row for as long
    as the other demo runs (found 23/09/2026)."""
    want = _proof_def()
    try:
        cur = system.tag.getConfiguration(PROOF_PATH, False)
        cur = dict(cur[0]) if cur else {}
    except (Exception, JThrowable):
        cur = {}
    import demo_control
    live = demo_control.demo_live("redundancy")
    if live == "unknown":
        # wd-control not answering: leave history as it is rather than guess.
        want["historyEnabled"] = bool(cur.get("historyEnabled", True))
    else:
        want["historyEnabled"] = live in ("running", "starting", "unhealthy")
    diff = []
    for k in ("expression", "dataType", "executionMode", "historyEnabled",
              "historyProvider", "sampleMode"):
        if unicode(cur.get(k)) != unicode(want[k]):
            diff.append(k)
    try:
        if int(cur.get("executionRate") or 0) != PROOF_RATE_MS:
            diff.append("executionRate")
    except (Exception, JThrowable):
        diff.append("executionRate")
    if not diff:
        return "unchanged"
    system.tag.configure("[default]", [{"name": PROOF_FOLDER, "tagType": "Folder",
                                        "tags": [want]}], "m")
    _logger().info("proof tag %s written (%s)" % (PROOF_PATH, ", ".join(diff)))
    return "written: " + ", ".join(diff)


def proof_seconds(frm, to):
    """Which seconds in [frm, to) THIS half's historian holds for the proof tag.

    Judged by VALUE, not by timestamp: the value is the second it was taken in,
    so a row counts for the second it names. `rows` is the raw count, so a
    duplicate shows as rows > len(seconds)."""
    frm, to = int(frm), int(to)
    ds = system.tag.queryTagHistory(
        paths=[PROOF_PATH],
        startDate=system.date.fromMillis((frm - 2) * 1000),
        endDate=system.date.fromMillis((to + 2) * 1000),
        returnSize=-1, aggregationMode="LastValue", returnFormat="Wide",
        noInterpolation=True, includeBoundingValues=False)
    seconds = set()
    stamps = {}
    rows = 0
    for r in range(ds.getRowCount()):
        v = ds.getValueAt(r, 1)
        if v is None:
            continue
        try:
            v = int(v)
        except (Exception, JThrowable):
            continue
        if frm <= v < to:
            rows += 1
            seconds.add(v)
            t = ds.getValueAt(r, 0)
            try:
                stamps[v] = long(t.getTime())
            except (Exception, JThrowable):
                pass
    return sorted(seconds), rows, stamps


def handle_proof_get(request):
    """GET /system/webdev/GatewayAdmin/redundancy/proof?from=<s>&to=<s>

    Open and read-only like `guards`: seconds and this half's role, no secret.
    Answers on BOTH halves -- WebDev runs on a Warm standby (measured
    22/09/2026), which is what lets the recorder ask each historian."""
    try:
        params = request.get("params") or {}

        def one(name, dflt):
            v = params.get(name)
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            return int(v) if v not in (None, "") else dflt

        only = params.get("only")
        if isinstance(only, (list, tuple)):
            only = only[0] if only else None
        if only == "recovery":
            return {"json": {"ok": True, "recovery": _recovery_local()}}
        now_s = int(_now() / 1000)
        to = one("to", now_s)
        frm = one("from", to - 120)
        if to <= frm or to - frm > PROOF_MAX_WINDOW_S:
            return {"json": {"ok": False, "message": "window must be 1 to %d s"
                             % PROOF_MAX_WINDOW_S}}
        seconds, rows, stamps = proof_seconds(frm, to)
        me = local()
        # This half's own redundancy log for the window, raw millis and raw
        # words (events() keeps both): the recorder reads the moment the
        # request reached the gateway, and the moment each half changed level,
        # from here rather than from its own polls where the gateway knows.
        lo, hi = (frm - 60) * 1000, (to + 5) * 1000
        log = [{"ts": e["ts"], "text": e.get("raw", "")}
               for e in events(60) if lo <= e["ts"] <= hi]
        return {"json": {
            "ok": True, "half": "master" if me["isMaster"] else "backup",
            "active": bool(me["isActive"]), "from": frm, "to": to,
            "seconds": seconds, "rows": rows,
            # value -> the millis it was stored at, for "first row after".
            "stamps": dict((str(k), v) for k, v in stamps.items()),
            "events": log}}
    except (Exception, JThrowable), e:
        _logger().warn("proof query failed: %s" % e)
        return {"json": {"ok": False, "message": str(e)[:200]}}


_KIND_WORDS = {"planned": "Planned", "unplanned": "Unplanned",
               "automatic": "Master back"}
# What the changeover figure is timed FROM, per kind (control/redproof.py).
_KIND_FROM = {"planned": "from the request",
              "unplanned": "from the %s last seen in charge",
              "automatic": "from the %s standing down"}


def _secs(ms):
    """A duration as measured: milliseconds under a second, never rounded
    up into a figure the recorder did not see."""
    ms = max(0, int(ms or 0))
    return ("%d ms" % ms) if ms < 1000 else ("%.1f s" % (ms / 1000.0))


def _hhmmss(ms):
    return system.date.format(system.date.fromMillis(long(ms)), "HH:mm:ss")


def _proof_blank(stopped, why):
    dash = u"—"
    return {"has": False, "title": "LAST CHANGEOVER", "meta": why,
            "changeText": dash, "changeSub": "", "missingText": dash,
            "missingSub": "", "missingLevel": "", "why": "",
            "sources": {"master": [], "backup": [], "shade": []},
            "names": {"master": "Master", "backup": "Backup"},
            "rows": [], "stopped": stopped}


def proof(limit=3):
    """The Last changeover card and the rail's two facts, from wd-control's
    recorder (/state `redundancy`). Shows exactly what was measured: a figure
    that has not been measured yet is a dash, and missing seconds carry their
    count and reason. Never raises."""
    try:
        out = _proof(limit)
    except (Exception, JThrowable), e:
        _logger().warn("proof failed: %s" % e)
        out = _proof_blank(False, "unavailable: %s" % str(e)[:60])
    # The reason when seconds are missing, else what the figure is.
    out["missingLine"] = out.get("why") or out.get("missingSub") or ""
    # Words in place of the chart whenever it has nothing to draw: an empty
    # frame read as broken while a changeover was still being counted.
    src = out.get("sources") or {}
    if src.get("master") or src.get("backup"):
        out["chartNote"] = ""
    elif not out.get("chartNote"):
        if out.get("has"):
            out["chartNote"] = "No seconds were stored around this changeover"
        else:
            out["chartNote"] = out.get("meta") or "Nothing yet"
    if out.get("chartNote") == "Nothing yet":
        out["chartNote"] = "Nothing yet -- a changeover draws here"
    # Fixed length: the view binds rows[0..2] by index, and an index past the
    # end is a binding error -- a red box in front of a customer.
    rows = list(out.get("rows") or [])[:3]
    while len(rows) < 3:
        rows.append({"when": "", "what": "", "took": "", "lost": "", "level": ""})
    out["rows"] = rows
    return out


def _proof(limit):
    import demo_control
    stopped = _stopped()
    raw = demo_control._state()
    if raw is None or "error" in raw:
        return _proof_blank(stopped, "the recorder (wd-control) is not answering")
    block = raw.get("redundancy")
    if block is None:
        return _proof_blank(stopped, "wd-control is too old to record changeovers")
    recs = block.get("changeovers") or []
    # The headline is the last changeover someone ASKED for. After a hard
    # stop the master takes back automatically a minute later, and that
    # take-back (0 ms, the halves overlap) would otherwise replace the run
    # the viewer just watched. Take-backs stay in the list below.
    head = 0
    for i, r in enumerate(recs):
        if r.get("kind") != "automatic":
            head = i
            break
    rows = []
    for r in (recs[:head] + recs[head + 1:])[:int(limit)]:
        rows.append({
            "when": _hhmmss(r.get("at")),
            "what": "%s, %s" % (_KIND_WORDS.get(r.get("kind"), r.get("kind")),
                                (r.get("direction") or "").replace(" -> ", u" → ")),
            "took": _secs(r.get("activeAfterMs")),
            "lost": ("%d missing" % r["missing"]) if r.get("state") == "done"
                    else "counting",
            "level": ("bad" if r.get("missing") else "good")
                     if r.get("state") == "done" else "neutral"})
    if not recs:
        out = _proof_blank(stopped, "Nothing yet")
        out["rows"] = rows
        return out
    r = recs[head]
    kind = r.get("kind") or ""
    old = r.get("from") or ""
    out = _proof_blank(stopped, "")
    out["has"] = True
    out["rows"] = rows
    out["meta"] = "%s, %s, %s" % (
        _KIND_WORDS.get(kind, kind),
        (r.get("direction") or "").replace(" -> ", u" → "), _hhmmss(r.get("at")))
    out["changeText"] = _secs(r.get("activeAfterMs"))
    frm_words = _KIND_FROM.get(kind, "")
    out["changeSub"] = (frm_words % old) if "%s" in frm_words else frm_words
    w = r.get("window") or {}
    frm, to = int(w.get("from") or 0), int(w.get("to") or 0)
    if r.get("state") != "done":
        # Judged WINDOW_AFTER seconds after the changeover, once both halves'
        # historians can be asked -- a count before then would be a guess.
        out["missingSub"] = "counted at %s" % _hhmmss((to + 5) * 1000)
        out["chartNote"] = ("Drawn at %s, once both halves report which "
                            "seconds they stored" % _hhmmss((to + 5) * 1000))
        return out
    expected = int(r.get("expected") or 0)
    missing = int(r.get("missing") or 0)
    out["missingText"] = "%d of %d" % (missing, expected)
    out["missingSub"] = ""
    out["missingLevel"] = "bad" if missing else "good"
    if missing:
        out["why"] = "%d missing: %s" % (missing, r.get("missingWhy") or "not stored")
    silent = sorted((r.get("unanswered") or {}).keys())
    if silent:
        out["why"] = (out["why"] + "; " if out["why"] else "") + \
            "the %s historian did not answer" % " and ".join(silent)
    # The seconds stored, as one line split by which half was in charge --
    # the old half before the changeover, the new one after -- broken
    # wherever nothing was stored. Both halves write to the one historian, so
    # it is who was in charge that matters, not which half answered. The x
    # axis is seconds from the changeover, not clock time: an XY chart draws
    # dates in the VIEWER's time zone while the list beside it is the
    # gateway's, and a relative axis needs neither. The changeover is shaded.
    s0 = int(r.get("at") or 0) // 1000
    s1 = max(s0, int(r.get("newLoggedActiveAt") or r.get("newActiveAt") or 0) // 1000)
    cut = int(r.get("newActiveAt") or 0) // 1000
    held = set()
    for runs in (r.get("ranges") or {}).values():
        for a, b in runs or []:
            held.update(range(max(int(a), frm), min(int(b), to - 1) + 1))
    sources = {"master": [], "backup": [], "shade": []}
    counts = {"master": 0, "backup": 0}
    for s in sorted(held):
        half = r.get("from") if s < cut else r.get("to")
        if half not in sources:
            continue
        pts = sources[half]
        if pts and pts[-1]["v"] is not None and pts[-1]["t"] != s - 1 - s0:
            pts.append({"t": s - 1 - s0, "v": None})
        pts.append({"t": s - s0, "v": s - frm})
        counts[half] += 1
    sources["shade"] = [{"t0": -0.5, "t": s1 - s0 + 0.5, "v": expected}]
    out["sources"] = sources
    out["names"] = {"master": "Master in charge  %d s" % counts["master"],
                    "backup": "Backup in charge  %d s" % counts["backup"]}
    return out


def _plain(text):
    """One log line, as a customer would say it. Never invents."""
    low = (text or "").lower()
    for needle, phrase in _STORY_RULES:
        if needle in low:
            return phrase
    trimmed = (text or "").strip()
    return trimmed[:58] + ("..." if len(trimmed) > 58 else "")


def story(limit=5):
    """The whole page, reduced to what a customer needs to see.

    Never raises: it is wired straight to bindings, and a script transform that
    throws renders a red ERROR box and logs nothing anywhere a script can see.
    """
    try:
        d = summary()
        if not d.get("paired"):
            return {"paired": False, "badgeClass": "badge-warn",
                    "headline": "NO STANDBY",
                    "headlineLevel": "warn",
                    "sub": "This gateway is running on its own.",
                    "address": "", "badgeText": "NO STANDBY",
                    "masterName": "-", "masterSub": "", "masterPill": "",
                    "masterPillClass": "badge-neutral", "masterDim": "0.5",
                    "backupName": "-", "backupSub": "", "backupPill": "",
                    "backupPillClass": "badge-neutral", "backupDim": "0.5",
                    "controlling": "", "handoverLabel": "Hand over to the standby",
                "takesText": "--", "takesSub": "by config",
                "tookText": "--", "tookSub": "measured",
                "inStepText": "--", "inStepSub": "kept in step",
                "waitingText": "--", "waitingSub": "updates waiting",
                "handoverSub": "",
                    "localName": "this gateway",
                    "standbyReady": False, "syncing": False, "events": [{"what": "", "when": "", "level": "good"} for _ in range(5)]}

        # By FLAG, not by position: the cards are ordered master-then-backup so
        # the layout cannot jump during a handover, so an index says which ROLE a
        # card holds and never which half is local or in charge.
        cards = (d.get("cards") or []) + [{}, {}]
        me = ([c for c in cards if c.get("isLocal")] + [cards[0]])[0]
        them = ([c for c in cards if c is not me] + [{}])[0]
        active = ([c for c in cards if c.get("isActive")] + [me])[0]
        standby = them if active is me else me

        # THE TWO TILES ARE POSITIONS, NOT ROLES-OF-THE-MOMENT. Left is always
        # the master half, right is always the backup half, whichever of them
        # happens to be in charge -- the same reason `_summary()` fixes the card
        # order, and the road broke it by putting the ACTIVE half first. A
        # customer watching a handover then saw both tiles change places at the
        # same instant as the thing being demonstrated, which reads as the page
        # losing track rather than as responsibility moving.
        master_card = ([c for c in cards if c.get("role") == "Master"] + [me])[0]
        backup_card = ([c for c in cards if c.get("role") == "Backup"] + [them])[0]

        # A redundant pair shares ONE gateway name -- whichever half you ask
        # calls itself `Ignition-Standard` and only the PEER carries a suffix.
        # So the local card's label is the base name from either side, and the
        # tiles can be named the same way whoever is serving the page. Without
        # this the left tile would read `Ignition-Standard` when the master
        # serves and `Ignition-Standard-Master` when the backup does: the same
        # machine, two names, depending on who answered.
        base = me.get("label", "") or "Ignition"
        for suffix in ("-Master", "-Backup"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]

        # "PROTECTED" is the claim the demo exists to make, so it is only made
        # when it is actually true: a peer that is connected AND in step. A
        # half-synced pair saying PROTECTED in front of a customer is the one
        # outcome worth guarding against.
        # An EXPIRED standby is linked, in step and Warm -- every signal the
        # manager gives says ready -- and it would take over unlicensed. The
        # page used to say PROTECTED beside a banner saying "not ready"; both
        # cannot be true to a viewer, so the trial is part of the claim.
        expired = bool(d.get("standbyExpired"))
        standby_ok = (standby.get("stateLevel") == "good"
                      and bool(d.get("peerName")) and not expired)
        stopped = _stopped()
        # A STOPPED DEMO IS NOT A FAULT, and was drawn as one: the branch below
        # this reads "RUNNING ON ONE HALF" with a "NO STANDBY" pill, every word
        # true of a pair whose backup was simply never started, and the largest
        # thing on the page. The not-running state is now the loudest thing on
        # a stopped tab, and the pair's own state words wait until there is a
        # pair to describe.
        if stopped:
            headline, level = "NOT RUNNING", "warn"
            sub = "Start the Redundancy Demo on the Demos tab."
        elif not d.get("peerConnected") and me.get("isActive"):
            headline, level = "RUNNING ON ONE HALF", "warn"
            sub = "The other gateway is not answering. This one has everything."
        elif not d.get("peerName"):
            headline, level = "STANDBY UNREACHABLE", "bad"
            sub = "The spare cannot be seen from here."
        elif expired:
            headline, level = "STANDBY CANNOT TAKE OVER", "bad"
            # One line: a second one pushes the Try-it card off a 640px screen.
            sub = ("Linked and in step, but its trial has lapsed. Reset it at "
                   "console.test/_wd/trials.")
        elif d.get("pending"):
            headline, level = "CATCHING UP", "warn"
            sub = "Copying the latest changes to the standby."
        elif standby_ok:
            headline, level = "PROTECTED", "good"
            sub = "One address. Two gateways. No interruption."
        else:
            headline, level = "STANDBY NOT READY", "warn"
            sub = "The spare is not yet in step."

        cfg = _configured()
        evts = events(30)
        took_ago = _last_takeover(evts)
        outage = _measured_outage(d)
        if outage is not None:
            took_text = "%ds" % outage
            # NOT "detection time". lastSync is the last SYNC, which can predate
            # the last ping, so this figure also carries however long the dying
            # half took to stop answering -- measured 23s against a 10s ping
            # budget, with a container stop grace period inside it. Reporting it
            # as the detection time would make the configured number beside it
            # look wrong when it is not. "From last contact" is what it is.
            took_sub = "from last contact"
        elif took_ago:
            # The BIG number is always a duration. A time-ago in that slot,
            # beside "23s" meaning an outage, reads as one too.
            took_text = u"\u2014"
            took_sub = "last changeover %s ago" % took_ago
        else:
            took_text = u"\u2014"
            took_sub = "none in 30 min"
        facts = sync_facts()

        rows = []
        for e in evts:
            phrase = _plain(e.get("text", ""))
            # Collapse a run of identical phrases: the gateway repeats a
            # transition per provider, and five lines of "Gateway running" is
            # noise where one is a fact.
            if rows and rows[-1]["what"] == phrase:
                continue
            rows.append({"what": phrase, "when": e.get("ago") or e.get("at", ""),
                         "level": e.get("level", "good")})
            if len(rows) >= int(limit):
                break

        # Pad to a fixed length. The view binds five fixed rows by index rather
        # than repeating a template, so a short history must not leave
        # `events[4]` missing -- an index past the end is a binding error, and a
        # binding error on this page is a red box in front of a customer.
        if not rows:
            rows.append({"what": "Nothing yet", "when": "", "level": "good"})
        while len(rows) < int(limit):
            rows.append({"what": "", "when": "", "level": "good"})

        # The class SUFFIX, not the whole path. Views build a dynamic class as
        # `{session.custom.style} + '/status/' + <this>` -- the literal has to
        # end in a slash, which is how `make validate` tells a dynamic class
        # from a typo'd static one and skips it instead of failing the build.
        badge = {"good": "badge-ok", "bad": "badge-alarm"}.get(level, "badge-warn")

        # The pill must not simply repeat the headline six inches to its right.
        # It answers the second question instead: is there actually a spare.
        # Stopped, it says nothing at all -- the headline already does.
        badge_text = ("" if stopped else "STANDBY READY" if standby_ok
                      else ("STANDBY UNLICENSED" if expired else "NO STANDBY"))
        not_ready_word = ("Not started" if stopped
                          else "Trial lapsed" if expired else "Not ready")

        # The HOST, not the deep link. A customer is being shown "you type one
        # address and it keeps working"; the full /data/perspective/client/...
        # path is noise, and long enough to wrap the tile onto three lines.
        host = front_door().get("url", "")
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        host = host.split("/")[0]

        return {
            "paired": True,
            "badgeClass": badge,
            "headline": headline,
            "headlineLevel": level,
            "sub": sub,
            "address": host,
            "badgeText": badge_text,
            # LEFT is the master half, RIGHT is the backup half, always.
            "masterName": base,
            "masterSub": ("Serving this screen" if master_card.get("isActive")
                          else ("Ready to take over" if standby_ok else not_ready_word)),
            "masterPill": ("IN CONTROL" if master_card.get("isActive")
                           else "" if stopped else "STANDING BY"),
            "masterPillClass": "badge-ok" if master_card.get("isActive") else "badge-neutral",
            "masterDim": "1" if master_card.get("isActive") else "0.5",
            "backupName": base + "-Backup",
            "backupSub": ("Serving this screen" if backup_card.get("isActive")
                          else ("Ready to take over" if standby_ok else not_ready_word)),
            "backupPill": ("IN CONTROL" if backup_card.get("isActive")
                           else "" if stopped else "STANDING BY"),
            "backupPillClass": "badge-ok" if backup_card.get("isActive") else "badge-neutral",
            "backupDim": "1" if backup_card.get("isActive") else "0.5",
            # Said in words under the address, so the routing is not left to be
            # inferred from which tile looks brighter.
            "controlling": ("the %s half" % str(active.get("role", "")).lower()) if active else "",
            # HOW FAST, and WHAT SURVIVES -- the two questions that come straight
            # after "which half is serving me". Every figure is the gateway's
            # own: nothing here is timed by this page or estimated.
            "takesText": ("up to %ds" % cfg["seconds"]) if cfg["seconds"] else u"\u2014",
            "takesSub": "by config",
            "tookText": took_text,
            "tookSub": took_sub,
            "inStepText": ("%d parts" % len(facts.get("carried") or []))
                          if d.get("peerConnected") else u"\u2014",
            # Under the IN STEP and WAITING labels, "kept in step" and
            # "updates waiting" said each label again; a sub now says only
            # what the label cannot.
            "inStepSub": ("" if d.get("peerConnected")
                          else "not started" if stopped else "peer is gone"),
            "waitingText": "%d" % int(facts.get("pending_total") or 0),
            "waitingSub": "updates",
            # THE BUTTON HAS TO SAY WHICH WAY IT GOES. "Hand over to the
            # standby" is only true while the master holds it; pressed from the
            # backup it hands back, and a button that describes the opposite of
            # what it does is worse than an unlabelled one -- in front of a room
            # it invites the demonstrator to talk the audience through a move
            # that is not the one happening.
            "handoverLabel": ("Hand back to the master"
                              if backup_card.get("isActive")
                              else "Hand over to the standby"),
            # MEASURED, not assumed. The session is served by the half that was
            # active when it connected, so a handover drops it -- but Perspective
            # reconnects on its own and did so in ~30s here, through the front
            # door, with no reload. What it CANNOT carry across is where you
            # were: the reconnect is a NEW session, so it comes back on the
            # default tab. That is what makes it feel like it needs a refresh.
            "handoverSub": ("Planned. The master answers again; this screen "
                            "reconnects in about 30s."
                            if backup_card.get("isActive")
                            else "Planned. The other gateway answers; this screen "
                                 "reconnects in about 30s."),
            # The feed is THIS gateway's own log and nobody else's, so every
            # line in it is about this half -- which the rows themselves cannot
            # say, because the gateway does not name itself in them. Naming the
            # source once, in the heading, is what stops "Took charge" reading
            # as ambiguous; and after a handover the front door moves the
            # session to the other half, so the heading changes with it and the
            # customer can see that it did.
            "localName": me.get("label", "this gateway"),
            "standbyReady": standby_ok,
            "stopped": stopped,
            "syncing": standby_ok or (expired and standby.get("stateLevel") == "good"),
            "events": rows,
        }
    except (Exception, JThrowable), e:
        _logger().warn("story failed: " + str(e))
        return {"paired": False, "badgeClass": "badge-warn",
                "badgeText": "UNAVAILABLE", "headline": "UNAVAILABLE",
                "headlineLevel": "warn", "sub": str(e)[:80],
                "address": "", "standbyReady": False,
                "masterName": "-", "masterSub": "", "masterPill": "",
                "masterPillClass": "badge-neutral", "masterDim": "0.5",
                "backupName": "-", "backupSub": "", "backupPill": "",
                "backupPillClass": "badge-neutral", "backupDim": "0.5",
                "controlling": "", "handoverLabel": "Hand over to the standby",
                "takesText": "--", "takesSub": "by config",
                "tookText": "--", "tookSub": "measured",
                "inStepText": "--", "inStepSub": "kept in step",
                "waitingText": "--", "waitingSub": "updates waiting",
                "handoverSub": "",
                "localName": "this gateway",
                "syncing": False, "events": [{"what": "", "when": "", "level": "good"} for _ in range(5)]}
