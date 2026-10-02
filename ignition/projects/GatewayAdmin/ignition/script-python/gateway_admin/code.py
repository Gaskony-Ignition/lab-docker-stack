"""Live state of the three gateways, for the GatewayAdmin status page.

Answers two questions the demo needs on screen:

  1. Is each gateway up and talking to the hub?
  2. Does each edge actually have the latest template change, or is it behind?

Question 2 is the interesting one, and it is answerable entirely on the hub
without reaching out to the edges: compare WHEN the template resource last
changed against WHEN that agent was last pushed to. If the resource is newer
than the push, that edge is stale. This is the same reasoning that caught the
differential-push bug -- EAM reporting Success does not mean bytes moved, so
"pushed at" is recorded by us at the moment we call runTask, not read back from
EAM's task history.

Jython 2.7: no f-strings, and `except Exception` does not catch Java throwables,
so anything touching Java names both.
"""
from java.lang import Throwable as JThrowable

AGENTS = [
    {"agent": "Ignition-Edge1", "site": "Site1", "task": "Send Site1 to Ignition-Edge1",
     "ping": "http://ignition-edge1:8088/StatusPing",
     "trial": "http://ignition-edge1:8088/data/api/v1/trial"},
    {"agent": "Ignition-Edge2", "site": "Site2", "task": "Send Site2 to Ignition-Edge2",
     "ping": "http://ignition-edge2:8088/StatusPing",
     "trial": "http://ignition-edge2:8088/data/api/v1/trial"},
]

# Where we record the last successful push per agent. Lives beside the project
# data rather than in a database so the status page still works on a gateway
# with no DB connection -- same reasoning as demo_styles.get_current().
_STATE = "gateway_admin_pushes.json"

LOG = "GatewayAdmin"


def _logger():
    return system.util.getLogger(LOG)


def _state_path():
    """Absolute path of the push-record file, in the gateway's data dir."""
    import os
    from java.lang import System as JSystem

    for prop in ("ignition.installdir", "user.dir"):
        base = JSystem.getProperty(prop)
        if base and os.path.isdir(os.path.join(base, "data")):
            return os.path.join(base, "data", _STATE)
    return os.path.join("/usr/local/bin/ignition/data", _STATE)


def _read_state():
    import os
    import json

    path = _state_path()
    if not os.path.isfile(path):
        return {}
    try:
        handle = open(path, "r")
        try:
            return json.loads(handle.read() or "{}")
        finally:
            handle.close()
    except Exception, e:
        _logger().warn("could not read push state: " + str(e))
        return {}
    except JThrowable, e:
        _logger().warn("could not read push state: " + str(e))
        return {}


def _write_state(state):
    import json

    handle = open(_state_path(), "w")
    try:
        handle.write(json.dumps(state))
    finally:
        handle.close()


def _now_millis():
    from java.lang import System as JSystem
    return JSystem.currentTimeMillis()


def _fmt(millis):
    from java.util import Date
    from java.text import SimpleDateFormat

    if not millis:
        return "never"
    return SimpleDateFormat("HH:mm:ss").format(Date(long(millis)))


def template_modified():
    """When the shared template resource last changed, in epoch millis.

    Reads the same resource.json timestamp that demo_styles.commit_pack()
    bumps, because that is exactly the value EAM diffs against.
    """
    import os
    import re
    from java.text import SimpleDateFormat
    from java.util import TimeZone

    import demo_styles
    base = demo_styles._projects_dir()
    if base is None:
        return 0

    # There is no single shared copy any more. Inheritance is single-parent and
    # this project, Site1 and Site2 are siblings under Styles_Template, so each
    # carries its own demo_styles and commit_pack() writes all of them at once.
    # Take the newest: they are written in one pass, and the newest is the one
    # an edge would be behind. Discovered rather than hardcoded -- pointing this
    # at a project name that no longer exists returned 0 silently, and the card
    # read "template changed never" while everything else looked right.
    fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'")
    fmt.setTimeZone(TimeZone.getTimeZone("UTC"))

    newest = 0
    for _name, folder in demo_styles._copies(base):
        path = os.path.join(folder, "resource.json")
        if not os.path.isfile(path):
            continue
        handle = open(path, "r")
        try:
            raw = handle.read()
        finally:
            handle.close()
        found = re.search(r'"timestamp"\s*:\s*"([^"]+)"', raw)
        if not found:
            continue
        try:
            millis = fmt.parse(found.group(1)).getTime()
        except (Exception, JThrowable):
            continue
        if millis > newest:
            newest = millis
    return newest


def _is_up(url):
    """True/False from the gateway's own /StatusPing, or None if unreachable.

    Deliberately NOT system.eam.queryAgentStatus(): that returned rows whose
    accessor names did not match anything we tried, so every edge read as
    UNKNOWN with no error to explain it. /StatusPing is unauthenticated, answers
    {"state":"RUNNING"}, and -- more usefully -- reports on the EDGE ITSELF
    rather than on EAM's opinion of it, which is the question the page asks.

    The hub reaches the edges by container name on the shared `backbone`
    network, so these URLs work from inside the gateway and would not from a
    browser. Short timeout: this runs on a binding thread.
    """
    try:
        # `timeout` is the only timeout kwarg system.net.httpClient takes.
        # connect_timeout/read_timeout raise TypeError, and swallowing that
        # silently is what made every edge read UNKNOWN with nothing logged.
        client = system.net.httpClient(timeout=2000)
        return "RUNNING" in client.get(url).text
    except Exception, e:
        _logger().debug("ping failed for " + url + ": " + str(e))
        return None
    except JThrowable, e:
        _logger().debug("ping failed for " + url + ": " + str(e))
        return None


# The page draws three cards and now a guard line from the same facts, so a
# naive implementation pings both edges four times per poll -- each with a 2s
# timeout, on binding threads, against gateways that may be down. One second of
# memo makes a poll one round of pings, and keeps every card and every button
# on ONE reading: a push refused because "Edge 1 is not answering" can never sit
# beside a card drawn from a ping that said it was.
_PING_CACHE = {"at": 0, "value": {}}
PING_TTL_MILLIS = 1000


def _agent_states(fresh=False):
    """agent name -> True / False / None (unreachable).

    `fresh` skips the memo, for the one caller that must not act on a reading up
    to a second old: push() refuses on this, and refusing to push to an edge
    that came up half a second ago would be its own small lie.
    """
    now = _now_millis()
    if not fresh and (now - _PING_CACHE["at"]) < PING_TTL_MILLIS:
        return _PING_CACHE["value"]
    out = {}
    for target in AGENTS:
        out[target["agent"]] = _is_up(target["ping"])
    _PING_CACHE["at"] = now
    _PING_CACHE["value"] = out
    return out


def _push_at(entry):
    """Millis from a push record. Older records were a bare int."""
    if isinstance(entry, dict):
        return entry.get("at", 0)
    return entry or 0


def _push_pack(entry):
    """Which pack went out on that push, or '' if the record predates this."""
    if isinstance(entry, dict):
        return entry.get("pack", "")
    return ""


def _edge_name(agent):
    """"Edge 1" rather than "Ignition-Edge1" -- a refusal is read out loud."""
    for target in AGENTS:
        if target["agent"] == agent:
            return target["site"].replace("Site", "Edge ")
    return agent


def record_push(agent, pack=""):
    """Remember that `agent` was pushed to just now, and with which pack.

    The pack is recorded because the page previously INFERRED an edge's theme --
    it showed the hub's current pack when it believed the edge was up to date and
    "?" otherwise. So committing a new theme instantly made both edges read "?",
    which looks like the page lost track of them. What actually went to an edge is
    only knowable at the moment we push, so record it then.
    """
    state = _read_state()
    state[agent] = {"at": _now_millis(), "pack": pack}
    _write_state(state)


def push(agents=None):
    """Run the Send Project task for one or both agents, and record the time.

    REFUSED FOR AN EDGE THAT IS NOT ANSWERING, and that is the whole point.
    EAM accepts a Send Project task for a disconnected agent perfectly happily
    -- it queues it -- so with the edges down this reported success and, because
    the record was written on runTask not raising, the card flipped to
    UP TO DATE for an edge that had received nothing. The module's own docstring
    at the top of this file says "EAM reporting Success does not mean bytes
    moved" and then did exactly that one level up.

    /StatusPing is the check, not EAM's opinion of the agent -- see _is_up.
    Returns a status string for the page. Never raises: it is wired to a button.
    """
    import demo_styles

    targets = [a for a in AGENTS if agents is None or a["agent"] in agents]
    if not targets:
        return "no matching agent"

    live = _agent_states(fresh=True)
    done = []
    for target in targets:
        agent = target["agent"]
        state = live.get(agent)
        if state is not True:
            done.append(
                "%s is %s -- nothing was sent to it. EAM would take the task "
                "and queue it, and the card would say UP TO DATE for an edge "
                "that never got it. Start the EAM Demo on the Demos tab."
                % (_edge_name(agent),
                   "not answering" if state is None else "down"))
            continue
        try:
            system.eam.runTask(target["task"], "eam", True)
            record_push(agent, demo_styles.chosen_pack())
            done.append("%s sent -- the card says SENT until the edge reports "
                        "the new theme back" % _edge_name(agent))
        except Exception, e:
            done.append("FAILED " + _edge_name(agent) + ": " + str(e))
        except JThrowable, e:
            done.append("FAILED " + _edge_name(agent) + ": " + str(e))
    return " | ".join(done)


def _trial_of(url):
    """Trial minutes for a gateway, or None if it cannot be read.

    GET /data/api/v1/trial is an OPEN_ROUTE on every gateway -- readable with no
    credentials at all -- which is the only reason the hub can report the edges'
    trials without holding a login for each of them.
    """
    if url is None:
        import gw_trial
        left = gw_trial.seconds_left()
        return None if left < 0 else left // 60
    try:
        body = system.net.httpClient(timeout=4000).get(url).json
        if body.get("expired"):
            return 0
        return int(body.get("trialSecondsLeft", 0)) // 60
    except (Exception, JThrowable), e:
        _logger().warn("trial read failed for %s: %s" % (url, e))
        return None


def _trial_fields(mins):
    """Text + severity for the card. Under 30 min is a warning: the trial lapses
    then, and a person resets it (docs/TRIALS.md)."""
    if mins is None:
        return {"trial": "?", "trialLevel": "neutral"}
    if mins <= 0:
        return {"trial": "EXPIRED", "trialLevel": "alarm"}
    if mins < 30:
        return {"trial": "%d min" % mins, "trialLevel": "warn"}
    return {"trial": "%d min" % mins, "trialLevel": "ok"}


# Four bindings on the EAM tab draw from this -- three cards and the guard line
# -- all on the same 3s expression, so without a memo each poll repeats two
# trial reads, two pings and two tag reads within milliseconds of itself. A TTL
# well under the poll interval keeps every card and every button on one
# consistent reading while still being live: the same bargain sf_demo.summary()
# strikes, and for the same reason.
_SNAP_CACHE = {"at": 0, "value": None}
SNAPSHOT_TTL_MILLIS = 1500


def snapshot():
    """Everything the status page renders, as plain dicts."""
    now = _now_millis()
    if (_SNAP_CACHE["value"] is not None
            and (now - _SNAP_CACHE["at"]) < SNAPSHOT_TTL_MILLIS):
        return _SNAP_CACHE["value"]
    value = _snapshot_uncached()
    _SNAP_CACHE["at"] = now
    _SNAP_CACHE["value"] = value
    return value


def _snapshot_uncached():
    import demo_styles

    modified = template_modified()
    pushes = _read_state()
    live = _agent_states()

    hub = {
        "id": "hub",
        "name": "Ignition-Standard",
        "role": "Hub - EAM Controller",
        "connected": True,
        "pack": demo_styles.chosen_pack(),
        "current": True,
        "sent": True,
        "confirmed": True,
        "observedPack": demo_styles.chosen_pack(),
        "detail": "template changed " + _fmt(modified),
    }
    hub.update(_trial_fields(_trial_of(None)))
    rows = [hub]

    for target in AGENTS:
        agent = target["agent"]
        entry = pushes.get(agent, 0)
        last = _push_at(entry)
        sent_pack = _push_pack(entry)
        # THE PROMISE AND THE OBSERVATION, KEPT APART. `sent` is our own record
        # that a task was handed to EAM; `observed` is what the edge says it is
        # wearing, read back over its own road. Only the second one can make
        # this card green. Blending them is what let an edge that received
        # nothing read UP TO DATE -- see push(), and the Sparkplug tab's
        # _engine_node_online for the rule this follows: where two ends of a
        # wire can disagree, show both and never render a remembered action as
        # an observed state.
        observed = _live_pack(agent)
        sent = bool(last) and last >= modified
        confirmed = bool(observed) and observed == sent_pack and sent
        # The card's second badge already says which of the four this is --
        # NEVER PUSHED, BEHIND, SENT, UP TO DATE -- so the footer carries only
        # what the badge cannot: when (the console's text budget, 21/09/2026).
        # The longer sentences each state used to print were that badge again.
        if not last:
            detail = "no push recorded since this page was installed"
        else:
            detail = "pushed " + _fmt(last)
        row = {
            "id": agent,
            "name": agent,
            "role": "Edge - " + target["site"],
            "connected": live.get(agent, None),
            # Read back from the EDGE where it can be, falling back to what we
            # last sent. Reading beats remembering: the push record is a promise
            # that says nothing about whether the push landed, and it cannot
            # notice a theme changed any other way. What it must NOT fall back
            # to is the hub's own theme -- inferring that made every edge read
            # "?" the moment a new one was committed, which looked like the page
            # losing track of them.
            "pack": (observed or sent_pack
                     or ("?" if not last else "(before packs were recorded)")),
            # `current` now means CONFIRMED, not "we called runTask". Everything
            # that paints this card green reads it.
            "current": confirmed,
            "sent": sent,
            "confirmed": confirmed,
            "observedPack": observed,
            "detail": detail,
        }
        row.update(_trial_fields(_trial_of(target["trial"])))
        rows.append(row)
    return rows


def _not_ready():
    """Why the EAM demo cannot be acted on yet, in the control plane's words.

    Imported lazily and never allowed to raise: this module is read by pages on
    a gateway that may not carry demo_control at all, and a guard line is not
    worth taking a page down for -- the action functions guard themselves as
    well (defence in depth; the page is not the only caller).
    """
    try:
        import demo_control
        return demo_control.ready_note("eam")
    except (Exception, JThrowable):
        return ""


def summary():
    """What the EAM tab's buttons are allowed to do, and why not.

    A `can` dict of {ok, why, label, busy} per button, ready to show -- the
    refusal wording is written here, where the reason is known, rather than
    assembled in a view expression that could disagree with the card beside it.
    Every button on the tab binds this and nothing else.

    Catches for itself, like card(): a transform that raises renders the bound
    component as a red ERROR box and logs nothing. Here that would be worse
    than a wrong answer -- `ok` would be unreadable and every button on the tab
    would be drawn with no enabled state at all.
    """
    try:
        return _summary()
    except (Exception, JThrowable), exc:
        _logger().warn("EAM guards failed: " + str(exc))
        import demo_action
        blocked = demo_action.can(False, "the console cannot tell what state "
                                         "the edges are in: " + str(exc)[:80])
        return {"can": dict((k, blocked) for k in
                            ("pushBoth", "pushE1", "pushE2", "resetTrials",
                             "commitTheme")),
                "notReady": ""}


def _summary():
    import demo_action

    not_ready = _not_ready()
    live = _agent_states()

    def edge_can(agent, label):
        state = live.get(agent)
        if not_ready:
            return demo_action.can(False, not_ready, label, "Pushing...", note="")
        if state is not True:
            # One clause. The longer reason -- EAM would queue the task and
            # the card would then claim a push that never landed -- is why this
            # refuses at all, and is in the docstring of push().
            return demo_action.can(
                False, "%s is %s" % (
                    _edge_name(agent),
                    "not answering" if state is None else "down"),
                label, "Pushing...")
        return demo_action.can(True, label=label, busy_label="Pushing...")

    e1, e2 = AGENTS[0]["agent"], AGENTS[1]["agent"]
    both_down = [a for a in (e1, e2) if live.get(a) is not True]
    if not_ready:
        both = demo_action.can(False, not_ready, "Push both edges", "Pushing...",
                              note="")
    elif both_down:
        both = demo_action.can(
            False, "%s %s not answering"
                   % (" and ".join([_edge_name(a) for a in both_down]),
                      "is" if len(both_down) == 1 else "are"),
            "Push both edges", "Pushing...")
    else:
        both = demo_action.can(True, label="Push both edges",
                               busy_label="Pushing...")

    # The hub resets itself in-process and always can; the edges are ASKED over
    # the Gateway Network, and with both down the click used to block ~28s
    # inside the handler with nothing on screen. It is off-thread now (see
    # demo_action), so the refusal is about honesty rather than about freezing:
    # there is nothing to reset but the hub.
    if not_ready:
        trials = demo_action.can(False, not_ready, "Reset trials",
                                 "Resetting trials...", note="")
    else:
        trials = demo_action.can(
            True, label="Reset trials", busy_label="Resetting trials...",
            note="resets the hub only" if both_down else "")

    return {
        "can": {
            "pushBoth": both,
            "pushE1": edge_can(e1, "Push Edge 1"),
            "pushE2": edge_can(e2, "Push Edge 2"),
            "resetTrials": trials,
            # Committing a theme is a local resource write on this gateway: it
            # needs no edge, no control plane and no reconcile, so it is
            # deliberately NOT guarded beyond the in-flight latch. What it does
            # need is to say that pushing is the half that carries it.
            # No standing note: "commit, then push" explains the UI rather
            # than the system, and the Guide's EAM steps say it where it is
            # read (text budget, 21/09/2026). The click still answers.
            "commitTheme": demo_action.can(
                True, label="Commit theme everywhere",
                busy_label="Committing..."),
        },
        "notReady": not_ready,
    }


# --- the card shape -----------------------------------------------------------
# Both admin pages draw the same card view, so both have to speak its parameters.
# See sf_demo.card() for the Store & Forward side; keeping the two in the same
# shape is what lets one GatewayCard serve both, rather than two that drift.

def card(which):
    """One gateway's card parameters. `which` is 'hub', or a 0-based edge index.

    Catches for itself. A Perspective script transform that raises renders the
    component as a red ERROR box and logs NOTHING -- the failure goes to the
    browser and nowhere a script can see it -- so three gateway cards can go
    dark with no way to find out why. Log it and render a card that says so.
    """
    try:
        return _card(which)
    except (Exception, JThrowable), exc:
        import traceback
        system.util.getLogger("gateway_admin").error(
            "card(%r) failed: %s\n%s" % (which, exc, traceback.format_exc()))
        return {"label": "Gateway", "sublabel": str(exc)[:60], "icon": "standard",
                "state": "SCRIPT ERROR", "stateLevel": "bad",
                "state2": "", "state2Level": "warn", "metrics": [], "footer": ""}


def _card(which):
    rows = snapshot()
    index = 0 if which == "hub" else int(which) + 1
    if index >= len(rows):
        return {"label": "Gateway", "sublabel": "", "icon": "edge",
                "state": "UNKNOWN", "stateLevel": "warn",
                "state2": "", "state2Level": "warn",
                "metrics": [], "footer": ""}

    row = rows[index]
    connected = row["connected"]
    if connected is True:
        state, level = "CONNECTED", "good"
    elif connected is False:
        state, level = "DOWN", "bad"
    else:
        state, level = "UNKNOWN", "warn"

    # FOUR STATES, NOT TWO, and only one of them is green. "UP TO DATE" used to
    # be printed from the push record alone -- a promise rendered as an
    # observation, and it failed GREEN, which is the direction that embarrasses
    # in front of a customer. SENT is neutral on purpose: it says what we did,
    # not what arrived.
    if row.get("confirmed"):
        state2, state2_level = "UP TO DATE", "good"
    elif row.get("sent"):
        state2, state2_level = "SENT", "warn"
    elif row.get("detail", "").startswith("no push recorded"):
        state2, state2_level = "NEVER PUSHED", "warn"
    else:
        state2, state2_level = "BEHIND", "warn"

    return {
        "label": row["name"],
        "sublabel": row["role"],
        "icon": "standard" if which == "hub" else "edge",
        "state": state,
        "stateLevel": level,
        "state2": state2,
        "state2Level": state2_level,
        "metrics": [
            {"value": row["pack"], "name": "THEME", "unit": "",
             "level": ""},
            {"value": row["trial"], "name": "TRIAL",
             "unit": "",
             # trialLevel is the badge vocabulary (ok/warn/alarm); the tile's is
             # good/warn/bad. One map rather than two spellings on the view.
             "level": {"alarm": "bad", "warn": "warn"}.get(row["trialLevel"], "")},
        ],
        # "no push recorded since this page was installed" under a badge that
        # already reads NEVER PUSHED was the same fact twice, eight words long.
        "footer": "" if state2 == "NEVER PUSHED" else row["detail"],
    }


# --- trials ---------------------------------------------------------------
# Unlicensed gateways run Perspective on a rolling 2-hour trial, and 8.3.9
# resets only one that has expired. gw_trial.reset_local() calls the licence
# manager in-process; it is inherited from Styles_Template, so EAM has carried it
# to both edges, where the resetTrial message handler calls it.
#
# So this button resets whichever of the three has lapsed and reports all
# three. Their GET /data/api/v1/trial is an OPEN_ROUTE -- readable with no
# credentials -- which is why the reporting half needs no authentication. A
# gateway whose sessions are already behind the trial screen is reset from the
# Trials page wd-control serves (https://console.test/_wd/trials).
TRIAL_URLS = [
    ("Ignition-Standard", None),
    ("Ignition-Edge1", "http://ignition-edge1:8088/data/api/v1/trial"),
    ("Ignition-Edge2", "http://ignition-edge2:8088/data/api/v1/trial"),
]

# The Gateway Network name of each edge, for the remote reset below. Same names
# the remote tag providers use -- the gateway name, not the connection id
# (`ignition-edge1|<uuid>`), which is what the incoming-connection record shows.
GAN_SERVERS = {
    "Ignition-Edge1": "ignition-edge1",
    "Ignition-Edge2": "ignition-edge2",
}

# The project the edges actually run. EAM's Send Project with "include inherited
# resources" lands everything as one project called `Edge`, whatever it was
# called here -- so this is not Site1/Site2.
EDGE_PROJECT = "Edge"


def _live_pack(agent):
    """What the edge says it is wearing, or "" -- see sf_demo.live_pack.

    Imported here rather than at module scope: gateway_admin is inherited by
    projects that do not carry sf_demo, and a missing import at module scope
    would take the whole library down rather than one field.
    """
    try:
        import sf_demo
        return sf_demo.live_pack(agent)
    except (Exception, JThrowable):
        return ""


def _reset_remote_trial(agent):
    """Ask an edge to reset its own trial, over the Gateway Network.

    Returns a short status string. Never raises: it is wired to a button.
    """
    server = GAN_SERVERS.get(agent)
    if not server:
        return "no Gateway Network name known"
    try:
        minutes = system.util.sendRequest(
            EDGE_PROJECT, "resetTrial", {}, remoteServer=server, timeoutSec=10)
        return "reset to %d min" % int(minutes)
    except (Exception, JThrowable), e:
        _logger().warn("remote trial reset failed for %s: %s" % (agent, e))
        return "reset FAILED (%s)" % e


def _remote_trial_minutes(url):
    try:
        client = system.net.httpClient(timeout=4000)
        body = client.get(url).json
        if body.get("expired"):
            return "EXPIRED"
        return "%d min" % (int(body.get("trialSecondsLeft", 0)) // 60)
    except (Exception, JThrowable), e:
        _logger().warn("trial read failed for %s: %s" % (url, e))
        return "unreachable"


def reset_trials():
    """Reset the hub's trial now, and report where all three stand.

    All three, not just the hub. The hub resets itself in-process; each edge is
    asked over the Gateway Network to reset itself, which needs no credentials
    on the hub at all -- see the resetTrial message handler in each Site
    for why that beats POSTing to the edge's own API.
    """
    import gw_trial

    parts = []
    failed = []
    for name, url in TRIAL_URLS:
        if url is None:
            mins = gw_trial.reset_local()
            parts.append("%s reset to %d min" % (name, mins) if mins >= 0
                         else "%s reset FAILED" % name)
            continue

        state = _reset_remote_trial(name)
        if state.startswith("reset to"):
            parts.append("%s %s" % (name, state))
        else:
            # Fall back to reporting what it says about itself -- that route is
            # an OPEN_ROUTE and works even when the request path does not.
            parts.append("%s %s (%s)" % (name, _remote_trial_minutes(url), state))
            failed.append(name)

    message = "Trials: " + ", ".join(parts) + "."
    if failed:
        message += (" %s did not answer over the Gateway Network -- "
                    "`make trial-reset` on the host drives its UI instead."
                    % " and ".join(failed))
    return message


# --- store & forward demo -------------------------------------------------
# What the hub can see of each edge's MQTT stream. The values arrive in the
# `MQTT Engine` tag provider, which MQTT Engine populates from the Sparkplug
# DBIRTH/DDATA the edges publish to MQTT Distributor on the hub pair
# (ssl://mqtt-master:8883, failing over to mqtt-backup).
#
# Paths are discovered rather than hard-coded: the edge node id is generated by
# Transmission (e.g. "Edge Node e055db"), so hard-coding it would break the page
# the first time an edge is rebuilt.
SF_ROOT = "[MQTT Engine]Edge Nodes"
SF_GROUP = "My MQTT Group"
SF_DEVICE = "Demo"
SF_SIGNALS = ["FlowRate", "Pressure", "Temperature"]

_DESCRIBED = False


# system.tag.browse() results are DICTS in 8.3, not objects -- n['name'], not
# n.getName(). Getting that wrong fails silently into an empty page.
def _browse(path):
    try:
        return [str(n["name"]) for n in system.tag.browse(path).getResults()]
    except (Exception, JThrowable), e:
        _logger().warn("browse failed at %r: %s" % (path, e))
        return []


def describe_mqtt_tree(depth=3):
    """Log the real shape of the MQTT Engine provider.

    MQTT Engine's folder layout is not something to assume -- it varies with the
    namespace settings on the module. Guessing it cost one empty page already, so
    write down what is actually there and read it from the log.
    """
    # Only folders. Browsing everything descends into each tag's PROPERTIES
    # (EngUnit, Quality, ReadOnly...) and buries the structure in noise.
    def folders(path):
        try:
            return [str(n["name"]) for n in system.tag.browse(path).getResults()
                    if str(n.get("tagType", "")) == "Folder"]
        except (Exception, JThrowable), e:
            _logger().warn("browse failed at %r: %s" % (path, e))
            return []

    def walk(path, level, prefix):
        for name in folders(path):
            child = path + name if path.endswith("]") else path + "/" + name
            _logger().info("mqtt folder: %s" % child)
            if level < depth:
                walk(child, level + 1, prefix + "  ")
    walk("[MQTT Engine]", 1, "")


def _edge_nodes():
    """Every edge node MQTT Engine currently knows about."""
    # No logging here: the page polls this every couple of seconds.
    return sorted(_browse("%s/%s" % (SF_ROOT, SF_GROUP)))


def stream_status():
    """One row per edge node: its Demo signals as MQTT Engine currently holds them.

    Drives the Store & Forward page. Returns quality as well as value, because
    the whole point of the demo is what happens when the values STOP being good.
    """
    global _DESCRIBED
    rows = []
    nodes = _edge_nodes()
    if not nodes and not _DESCRIBED:
        # Once only: this is polled every couple of seconds and a tree dump per
        # tick buries the log.
        _DESCRIBED = True
        describe_mqtt_tree()
    for node in nodes:
        paths = ["%s/%s/%s/%s/%s" % (SF_ROOT, SF_GROUP, node, SF_DEVICE, s)
                 for s in SF_SIGNALS]
        try:
            qvs = system.tag.readBlocking(paths)
        except (Exception, JThrowable), e:
            _logger().warn("read failed for %s: %s" % (node, e))
            continue
        vals = {}
        good = 0
        for name, qv in zip(SF_SIGNALS, qvs):
            ok = qv.quality.isGood()
            if ok:
                good += 1
            vals[name] = {"value": qv.value, "good": ok,
                          "stamp": _fmt(qv.timestamp.getTime()) if qv.timestamp else ""}
        rows.append({"node": node, "signals": vals,
                     "goodCount": good, "total": len(SF_SIGNALS),
                     "live": good == len(SF_SIGNALS)})
    return rows
