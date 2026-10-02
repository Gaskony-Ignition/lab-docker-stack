"""The demo console, for the Demos page: what exists, and what you want running.

The stack is twelve stacks and about 10 GB, and no single demonstration needs
more than a third of it. So the core stays up -- the front door, this gateway,
and the control plane -- and everything else is started from this page when it
is about to be shown, and stopped when it is not.

WHAT THIS PAGE DOES NOT DO

It does not start or stop stacks. It records which DEMOS you want, and
wd-control reconciles the machine to match: core, plus everything the wanted
demos need, up; anything else this repo owns, down.

That is not a stylistic preference. Stacks are shared -- both edges carry the
EAM push and the store-and-forward road -- so "stop store & forward" must not
stop the gateways the EAM demonstration is still using.
Reference-counting that in a button handler is arithmetic that is right until
the day it is not, in front of an audience.

It also cannot stop the core, and not because the buttons are hidden: the core
is always in the wanted set, so there is no request this page can make that
would take it down. A page able to stop the gateway serving it will, once.
`make down` in a terminal still stops everything.

Jython 2.7: no f-strings, `except Exception, e:`, and `except Exception` does
NOT catch Java throwables, so anything touching Java names both.
"""
import ui_creds
from java.lang import Throwable as JThrowable

LOG = "DemoConsole"

# By container name on `backbone`, like every other in-stack address.
CONTROL = "http://wd-control:8080"

# Reading is quick. A reconcile is not -- a gateway takes a minute to serve --
# but the POST only STARTS the job and returns, so both are short. The page
# watches the job through /state, which is what makes the wait honest instead of
# a frozen button.
READ_TIMEOUT = 8000
ACTION_TIMEOUT = 15000


def _logger():
    return system.util.getLogger(LOG)


def _call(method, path, timeout=READ_TIMEOUT):
    """One HTTP call to wd-control. Never raises -- a control plane that is down
    is a state this page has to render, not an error that blanks it.

    A failure carries the STATUS as well as a sentence, because the status is
    the only thing that distinguishes the two refusals this page has to word
    differently: 409 is "something else is running" and 400 is "that is not on
    offer any more". Without it both arrived as the literal string
    `wd-control returned HTTP 409`, on a page whose whole job is to explain
    itself, and a refused start reported "could not reach the control plane" --
    which is a different problem with a different fix.
    """
    try:
        client = system.net.httpClient(timeout=timeout)
        if method == "GET":
            response = client.get(CONTROL + path)
        else:
            response = client.post(CONTROL + path, data="")
        if response.good:
            return response.json
        return {"error": "wd-control returned HTTP " + str(response.statusCode),
                "status": response.statusCode}
    except JThrowable, jt:
        _logger().warn("wd-control unreachable: " + str(jt))
        return {"error": "wd-control is not reachable"}
    except Exception, e:
        _logger().warn("wd-control call failed: " + str(e))
        return {"error": str(e)}


# GET /state is now wanted by TWO pollers on their own 3s clocks -- the Demos
# page's cards and the console header's tab enabling -- and they are separate
# views, so nothing coordinates them. The memo collapses the pair of calls that
# land together into one. The TTL is deliberately well under either poll
# interval: it must never serve a reading old enough for a tab to disagree with
# the card that started the demo, which is the whole thing this pairing exists
# to prevent.
_STATE = {"at": 0, "value": None}
_STATE_TTL_MS = 1000
_FAIL = {"at": 0, "value": None}
_FAIL_TTL_MS = 3000


def _state():
    now = system.date.toMillis(system.date.now())
    if _STATE["value"] is not None and (now - _STATE["at"]) < _STATE_TTL_MS:
        return _STATE["value"]
    # A failure IS remembered, briefly. Every demo tab's guards now ask
    # ready_note(), so a control plane that hangs rather than refuses cost one
    # full READ_TIMEOUT per caller per poll -- measured 18/09/2026 with
    # wd-control wedged: the guards probe took over 90s. Three seconds is one
    # poll, so the control plane coming back still shows on the next one.
    if _FAIL["value"] is not None and (now - _FAIL["at"]) < _FAIL_TTL_MS:
        return _FAIL["value"]
    raw = _call("GET", "/state")
    if raw is not None and "error" not in raw:
        _STATE["at"] = now
        _STATE["value"] = raw
        _FAIL["value"] = None
    else:
        _FAIL["at"] = system.date.toMillis(system.date.now())
        _FAIL["value"] = raw
    return raw


# The console's tabs, by the key demos.json uses, in the order Main's tab
# container holds its children. Index IS the tab index -- the same list shape
# demo_guide.TAB_TOPICS uses, for the same reason: a tab is addressed by
# position, so the position has to be written down exactly once.
#            0=EAM   1=Store&Fwd     2=Architecture 3=Redundancy 4=Demos  5=MQTT
# The key stays `sparkplug` (it is the demo's id, in demos.json and every URL);
# only the LABEL people read became MQTT.
TAB_KEYS = ["eam", "storeforward", None,          "redundancy", None,    "sparkplug"]

TAB_LABELS = {"eam": "EAM", "storeforward": "Store & Forward",
              "redundancy": "Redundancy", "sparkplug": "MQTT"}


def _job_names(raw, demo_id):
    """Is the reconcile in flight the one that is about to change THIS demo.

    `job.what` is plain words ("starting eam"), written by the control plane for
    the page to print, and the demo id is in it. Matching on the id rather than
    on a job being in flight at all is what stops a start of one demo from
    reporting every other demo as mid-change -- which would shut the Sparkplug
    tab every time somebody started the EAM one.
    """
    job = raw.get("job") or {}
    if job.get("state") != "working":
        return False
    return str(demo_id or "") in str(job.get("what") or "")


def _readiness_of(d, raw):
    """(ready, plain words for why not) for one demo.

    The CONTROL PLANE owns this verdict -- it is the half that can shell into a
    gateway and ask whether it is past COMMISSIONING, which no binding on this
    page can do. So `ready`/`readyWhy` are used the moment they appear in
    /state, and until they do this falls back to what the page CAN see: every
    stack up and healthy, and no reconcile in flight for this demo.

    The fallback is deliberately weaker than the real gate and says so in
    plainer words. What it must not do is claim more than it knows: a container
    healthcheck passes during COMMISSIONING -- `wait_for_gateway` in lib.sh
    knows it, and that is the whole reason the verdict belongs upstream.
    """
    live = d.get("live", "stopped")
    title = d.get("title") or d.get("id") or "this demo"
    up = d.get("stacks_up", 0)
    total = d.get("stack_count", len(d.get("stacks") or []))

    # Nobody asked for it: "partial" here is a stack another demo shares
    # (postgres), and listing the stacks that are down clipped the card.
    if d.get("wanted") is False and live in ("stopped", "partial"):
        return False, "%s is not running" % title

    if "ready" in d:
        if d.get("ready"):
            return True, ""
        why = str(d.get("readyWhy") or "").strip()
        if why and _our_cut(d, raw, why):
            # A CUT THIS CONSOLE MADE IS THE DEMO WORKING. The control plane's
            # readiness check sees an edge off wd-mqtt and says "not ready" --
            # which is what the MQTT tab's "Cut Edge 3 link" and Store &
            # Forward's cut are FOR. Believing it disabled every button on the
            # tab mid-demonstration, Restore included, and put a "not ready"
            # banner over the outage being shown (found 21/09/2026). A cut is
            # time-boxed by wd-control itself (its restore loop), so once the
            # deadline passes this no longer applies and a stack still off the
            # network reads not ready again, as it should.
            return True, ""
        if why:
            return False, why

    if _job_names(raw, d.get("id")):
        what = str((raw.get("job") or {}).get("what") or "a reconcile")
        return False, "%s -- give it a minute" % what
    if live == "starting":
        return False, "%s is still starting -- %d of %d up" % (title, up, total)
    if live == "unhealthy":
        down = [s.get("stack", "?") for s in d.get("stacks") or []
                if s.get("health") == "unhealthy"]
        return False, "%s reports unhealthy" % (", ".join(down) or title)
    if live == "running":
        return True, ""
    if live == "partial":
        missing = [s.get("stack", "?") for s in d.get("stacks") or []
                   if not s.get("up")]
        return False, ("%s is only part way up -- %s %s not running"
                       % (title, ", ".join(missing) or "something",
                          "is" if len(missing) == 1 else "are"))
    return False, "%s is not running" % title


def _our_cut(d, raw, why):
    """Is `why` ONLY "a stack of this demo is off wd-mqtt", and is that either
    a cut this console is running, or no longer true?

    Two ways the control plane's verdict says it and is wrong for the page:

      a cut in progress  /state's mqtt.cuts holds it, with time left and
                         docker agreeing the stack is off the network -- the
                         demonstration working, not failing;
      a cut just over    the readiness run happened DURING the cut and its
                         verdict outlives it (it is pinned to whether each
                         gateway is serving, not to network membership), so
                         after Restore the tab stayed locked until the next
                         run. docker's own member list, sampled after that
                         run, says the stack is back.

    Anything else in `why`, or a stack still off the network with no cut to
    explain it, is left standing.
    """
    if "wd-mqtt" not in why:
        return False
    mq = (raw or {}).get("mqtt") or {}
    cuts = mq.get("cuts") or {}
    on = set(mq.get("onNetwork") or [])
    sampled = int(mq.get("sampledAt") or 0)
    checked = int(((raw or {}).get("verify") or {}).get("at") or 0)
    named = [s.get("stack") for s in (d.get("stacks") or [])
             if s.get("stack") and s.get("stack") in why]
    if not named:
        return False
    for stack in named:
        c = cuts.get(stack) or {}
        cut_now = bool(c.get("offNetwork")) and int(c.get("secondsLeft") or 0) > 0
        back_since = stack in on and sampled > checked
        if not (cut_now or back_since):
            return False
    return True


def ready_note(demo_id):
    """Plain words for why `demo_id` cannot be acted on yet, or "".

    The one function the demo modules ask, so a `can` flag on the EAM tab and
    the EAM tab button itself can never give two different reasons. Cheap: it
    reads the same memoised /state the cards do.
    """
    raw = _state()
    if raw is None or "error" in raw:
        return ""              # see tab_state: a status service being down
                               # must not lock the console
    for d in raw.get("demos", []):
        if d.get("id") == demo_id:
            ready, why = _readiness_of(d, raw)
            if ready:
                return ""
            # The control plane's words are terse ("starting eam", "not
            # started") because they were written for a card that already
            # carries the title. A banner on a tab has no such context.
            title = d.get("title") or demo_id
            if d.get("live", "stopped") == "stopped":
                # A stopped demo is not a wait -- nothing will change until
                # somebody starts it, so say where. The tab is open exactly so
                # this page can be read before deciding to start it (21/09/2026).
                #
                # It does NOT promise the page is blank. These tabs also carry
                # the console's own gateway status -- the hub's trial, the
                # redundancy link, what the historian holds -- all of which is
                # true whether or not a demo is running, and a sentence saying
                # "everything here stays empty" was read as a contradiction of
                # it by the first reviewer who saw it.
                return "%s is not running. Start it on the Demos tab." % title
            if why.startswith(title):
                return why
            # One sentence, and about the system: "the buttons come on when
            # it is" explained the page to its reader (text budget, 21/09/2026).
            return "%s is not ready yet: %s." % (title, why)
    return ""


def demo_live(demo_id):
    """`demo_id`'s own `live` word from /state -- "stopped", "starting",
    "partial", "running", "unhealthy" -- or "unknown".

    For the few places a STOPPED demo must be drawn differently from a broken
    one. The Redundancy tab led a stopped page with a large "RUNNING ON ONE
    HALF / NO STANDBY": every word true of a pair whose backup was never
    started, and read by everybody as a fault (21/09/2026). Never raises.
    """
    try:
        raw = _state()
        if raw is None or "error" in raw:
            return "unknown"
        for d in raw.get("demos", []):
            if d.get("id") == demo_id:
                return str(d.get("live") or "stopped")
    except (Exception, JThrowable):
        pass
    return "unknown"


def tab_state():
    """{tab_key: {"enabled", "ready", "reason"}} for the console header.

    EVERY DEMO TAB IS ALWAYS OPEN. A tab used to be disabled until its demo was
    running, on the grounds that a page of dashes reads as broken. It also made
    the console un-explorable: you could not look at what a demo shows before
    deciding to spend ten minutes and a gigabyte starting it, which is exactly
    what somebody meeting the stack wants to do first (Nigel, 21/09/2026).

    `ready` is what the action buttons gate on, through `ready_note()`, and
    `reason` is the words -- on the header's tooltip, on the demo's card, and in
    the banner at the top of the demo's own tab. A stopped demo therefore reads
    as "not started, start it here", with its buttons off and its data areas
    empty, instead of vanishing behind a tab you cannot press.

    Nothing here disables a tab. `enabled` stays in the dict because every
    header button, cursor and opacity binding in Main reads it, and because a
    future reason to shut a tab (one the CONSOLE cannot serve, rather than one
    whose demo is merely stopped) would belong here and nowhere else.

    Keyed by tab rather than by demo id so the view binds a stable path
    ({view.custom.tabs.eam.enabled}); an index would have to be spelled into
    every binding and would silently follow the wrong tab if the children were
    ever reordered.
    """
    raw = _state()
    out = {}
    for key in TAB_LABELS:
        out[key] = {"enabled": True, "ready": True, "reason": "", "dot": ""}
    if raw is None or "error" in raw:
        # Control plane down: enable everything. Locking a demonstrator out of
        # the tabs because a status service is unreachable is a worse failure
        # than a tab that shows dashes.
        return out
    for d in raw.get("demos", []):
        key = d.get("tab") or ""
        if key not in out:
            continue
        live = d.get("live", "stopped")
        ready, why = _readiness_of(d, raw)
        if live not in ("running", "partial", "starting"):
            why = "%s is not running -- start it on the Demos tab" % d.get("title", key)
        # The header's status dot: green running, amber on its way up or
        # unwell, none when stopped. Not for "partial" -- that is usually a
        # stack another demo shares (postgres), not this demo starting.
        # Only for a demo somebody started: one that is up because another
        # demo's gateways cover it reads "Shared" on the Demos page, and a
        # green dot beside it said it had been started (Nigel, 30/09/2026).
        dot = {"running": "badge-ok", "starting": "badge-warn",
               "unhealthy": "badge-warn"}.get(live, "") if d.get("wanted") else ""
        out[key] = {"enabled": True, "ready": ready, "reason": why, "dot": dot}
    return out


def summary():
    """Everything the page renders, in one poll."""
    raw = _state()
    if raw is None or "error" in raw:
        message = "the demo console's control plane is not answering"
        if raw and raw.get("error"):
            message = raw["error"]
        return {
            "ok": False,
            "headline": message,
            "detail": "It is core, so it should be up: make up STACK=wd-control",
            "core_line": "",
            "busy": False,
            "job_line": "",
            "budget_line": "",
            # Padded for the same reason `demos` is: an unbound sub-path draws
            # a component's red error box, and a button whose guard is missing
            # would be a button with no enabled state at all -- which is the
            # defect this whole pass is about.
            "can": {"stopAll": _stop_all_can(False, {}, [],
                                             "the control plane is not "
                                             "answering, so nothing can be "
                                             "asked of it")},
            # Padded, not empty. The frame is what the page IS; collapsing it to
            # nothing during the one failure this branch exists to render turns
            # "the control plane is not answering" into "the console is blank",
            # which reads as a broken gateway rather than a stopped service.
            "demos": _padded([]),
            "core_links": [],
            # The bar and the rig strip are the page's two always-on bands, so
            # they have to have a not-known shape rather than be absent: an
            # unbound sub-path renders a component's error box, which is a worse
            # way to say "the control plane is down" than the headline above.
            "mem": _mem_bar({}, {}),
            "load": _load_pill({}),
            "ready": _readiness({}, [], 0),
            "progress": _progress({}, 0),
            "rig": [],
            # Not omitted. An unbound sub-path renders a component's error box,
            # which is a worse way to say "the control plane is down" than the
            # headline already does.
            "version": {"known": False, "text": "", "warn": False, "tip": ""},
            # Same reason: the stack chip and the newer-releases block bind
            # sub-paths of this, so it has a not-known shape, not an absence.
            "stack": {"known": False, "text": "", "warn": False, "tip": "",
                      "behind": 0, "newer": []},
            "trial": _trial_line(),
            "core_pill": {"text": "core ?", "ok": False},
        }

    core = raw.get("core") or {}
    job = raw.get("job") or {}
    busy = job.get("state") == "working"
    demos = [_demo(d, busy, raw) for d in raw.get("demos", [])]
    running = [d for d in demos if d["live"] in ("running", "starting")]

    host = raw.get("host") or {}
    return {
        "ok": True,
        "headline": _headline(running, job),
        "detail": _detail(demos),
        # The denominator, and the nudge. Both are "" when they do not apply,
        # so the view can bind meta.visible to them and the rows disappear
        # rather than sitting there empty.
        "budget_line": _budget_line(host, raw.get("running") or {}, running),
        "core_line": _core_line(core, raw.get("running") or {}),
        "busy": job.get("state") == "working",
        "job_line": _job_line(job, raw.get("refused") or {},
                              raw.get("generated_at") or 0),
        "can": {"stopAll": _stop_all_can(busy, job, demos)},
        "demos": _padded(demos),
        # --- the two always-on bands ------------------------------------------
        # `budget_line` and `core_line` above are kept as the TOOLTIP text for
        # these: the reasoning behind the denominator, and which core stacks are
        # up by name, are both a sentence rather than a widget. What the widgets
        # add is that you can read them at a glance from across a room, which is
        # the only way anything on this page gets read during a demonstration.
        "mem": _mem_bar(host, raw.get("running") or {}),
        "load": _load_pill(host),
        "ready": _readiness(raw, demos, raw.get("generated_at") or 0),
        "progress": _progress(job, raw.get("generated_at") or 0),
        "rig": _rig(raw, set(s.get("stack") for s in core.get("stacks") or [])),
        "version": _version(raw),
        "stack": _stack(raw),
        "trial": _trial_line(),
        "core_pill": {
            "text": "core " + str(core.get("up", 0)) + "/" + str(core.get("count", 0)),
            "ok": core.get("up") == core.get("count") and core.get("count", 0) > 0,
        },
        # The CORE's own UIs, and the hub gateway is the one that matters: it
        # is the gateway serving this very page, and "jump in and look at a
        # setting" is nearly always about that one. It is not on any demo card
        # because the core is not a demo.
        "core_links": _links(core.get("stacks")),
    }


def _stop_all_can(busy, job, demos, blocked=""):
    """The bar's Stop-every-demo button. Same refusal as a card's, and one more:
    there is nothing to stop. It was refused with a 409 like any other second
    click, and told the reader at the page foot."""
    import demo_action

    label, gerund = "Stop every demo", "Stopping every demo..."
    if blocked:
        return demo_action.can(False, blocked, label, gerund)
    if busy:
        return demo_action.can(
            False, "the console is %s just now -- one reconcile at a time"
            % str(job.get("what") or "busy"), label, gerund,
            note="waiting: " + str(job.get("what") or "busy"))
    if not [d for d in demos if d.get("wanted")]:
        return demo_action.can(
            False, "nothing is running -- only the core is up", label, gerund)
    return demo_action.can(True, label=label, busy_label=gerund)


# ---------------------------------------------------------------- open links
#
# A demonstration regularly needs the gateway's own config UI on screen -- the
# Gateway Network page, EAM, the MQTT module, redundancy -- and every one of
# those detours used to cost a password lookup in front of the room. Each card
# carries the buttons instead.
#
# WHERE THE URLS COME FROM: the control plane, off the stack manifests
# (TEST_HOST). Nothing here lists a hostname, so a stack added by dropping a
# folder in stacks/ gets its button with no edit to this file.
#
# A GATEWAY BUTTON SIGNS YOU IN; the others just open. That is the control
# plane's `open` field, which points a gateway at the sign-in door and anything
# else at itself -- see control/service.py:stack_ui. The label says which,
# because a button that silently does one or the other is a button you stop
# trusting the first time it shows you a login page.

# Presentation only: an
# unmapped stack still gets a button, titled from its own name. The map exists
# so a card says "Edge 1 gateway" rather than "ignition-edge1", which is the
# name of a container and not the name of a thing in the demonstration.
# SHORT. Every one of these used to end in "gateway" -- "Edge 1 gateway",
# "Backup gateway" -- and the repeated noun was not free: at four
# tiles across, Store & Forward's four chips wrapped to a second row, which made
# every card in the grid 40px taller than its own content needed, because a row
# of tiles is as tall as its tallest member. Measured 24/08/2026: 269px against
# 229px with the nouns dropped. They are all buttons that open a thing; which
# kind of thing it is was never the useful half of the label.
_LABELS = {
    "ignition": "Hub",
    "ignition-edge1": "Edge 1",
    "ignition-edge2": "Edge 2",
    "ignition-edge3": "Edge 3",
    "ignition-edge4": "Edge 4",
    "ignition-backup": "Backup",
    "ignition-ha": "Front door",
    "npm": "Proxy",
}


def _label(stack):
    if stack in _LABELS:
        return _LABELS[stack]
    return stack.replace("-", " ").replace("_", " ").capitalize()


def _links(stacks):
    """The openable UIs among these stacks, in the order the demo lists them.

    A stack that is DOWN still gets its button, deliberately. The proxy answers
    a stopped .test name with a page naming the demo to start (the down page in
    stacks/npm), which is more use than a button that is not there -- and a
    button that appears and disappears as stacks come up is one people click
    twice while it is still starting.

    That was the stated intent and the view contradicted it for months: the
    button bound props.enabled to `up`, so the one thing the down page exists
    for could not be clicked. OpenLink is an ia.navigation.link now -- a real
    anchor, which has no enabled property at all, so the two agree by
    construction rather than by someone remembering.
    """
    out = []
    for s in stacks or []:
        url = s.get("open")
        if not url:
            continue
        stack = s.get("stack", "?")
        # Only offered where there is no sign-in door AND we hold a login. A
        # gateway never needs it -- it opens already signed in -- and offering
        # the key there would suggest the button had not worked.
        creds = (not s.get("gateway")) and ui_creds.has_credential(stack)
        out.append({
            "stack": stack,
            "label": _label(stack),
            "url": url,
            "up": bool(s.get("up")),
            "signin": bool(s.get("gateway")),
            "creds": creds,
            # "signs in" is a promise, so it is only made where the door
            # actually exists. Currently unsurfaced: it was the button's hover
            # tooltip, and ia.navigation.link has no tooltip property. Kept
            # because the promise is worth showing -- as a line under the
            # button rather than a tooltip, if it comes back.
            "note": "opens signed in" if s.get("gateway") else "opens",
        })
    return out


def _toggle_can(d, busy, raw, action, title):
    """Whether this card's one button may be pressed, and what it says.

    THIS IS THE REPORTED INCIDENT. Start EAM, the card reads Starting with a red
    Stop beside it, press Stop, and `control/service.py` refuses it with a 409
    -- correctly, one reconcile at a time -- which the page then printed in
    muted 12px at the page foot, 700px away. The refusal path was well built;
    the button was never disabled.

    A DEMO MID-RECONCILE IS THE ONLY REFUSAL HERE. Once the job has finished,
    stopping a demo whose gateways are still commissioning is a perfectly good
    request and the control plane accepts it, so "starting" does NOT disable
    this button -- only a job actually in flight does. Guarding more than the
    control plane refuses would be its own kind of lie.
    """
    import demo_action

    demo_busy = bool(d.get("busy")) if "busy" in d else False
    what = str(d.get("busyWhat") or "").strip()
    if not what:
        what = str((raw.get("job") or {}).get("what") or "").strip()

    if busy or demo_busy:
        doing = what or "another demo"
        # WHICH GERUND depends on the job, not on `wanted`: the control plane
        # records the want the moment Start is clicked, so `action` is already
        # "Stop" one second into a START. A card reading "Stopping..." through a
        # start is the same class of lie as the rest of this pass.
        gerund = action
        if str(d.get("id") or "") in what:
            if what.startswith("start"):
                gerund = "Starting..."
            elif what.startswith("stop"):
                gerund = "Stopping..."
        # The note is ONE SHORT LINE because it shares the button's row on a
        # tile a quarter of the page wide; the full sentence is the tooltip.
        return demo_action.can(
            False,
            "the console is %s just now -- one reconcile at a time" % doing,
            label=action, busy_label=gerund, note="waiting: " + doing)

    # Enabled, and still with something to say: a demo whose stacks are up but
    # which is not ready yet has every action on its tab switched off, and this
    # card is where somebody decides whether to go and look. Said here rather
    # than only in the tab button's tooltip, which a browser does not reliably
    # show.
    # Not while the control plane is merely re-checking a demo that was ready:
    # that happens on every verify run, and a note flickering onto every card
    # for a few seconds is how a line stops being read.
    # Only for a demo somebody started: an unasked-for one reads "partial"
    # when another demo shares a stack, and its Start button says enough.
    hint = ""
    if d.get("live", "stopped") != "stopped" and d.get("wanted"):
        ready, why = _readiness_of(d, raw)
        if not ready and not why.startswith("checking"):
            hint = why + " -- the buttons on its tab are off"

    return demo_action.can(True, label=action,
                           busy_label="Stopping..." if d.get("wanted")
                           else "Starting...",
                           note=hint)


def _demo(d, busy=False, raw=None):
    raw = raw or {}
    live = d.get("live", "stopped")
    wanted = bool(d.get("wanted"))
    stacks = d.get("stacks") or []
    up = d.get("stacks_up", 0)
    total = d.get("stack_count", len(stacks))

    # ONE OR TWO WORDS. The badge sits beside the title on a 320px card, and a
    # badge reading "Partly up for another demo" does not wrap -- it squeezes
    # the title into a five-line column instead. The stacks line underneath is
    # where the detail belongs.
    # Whether another demo you ASKED for is holding these stacks up. Without it
    # a demo that is up only because a neighbour needs it reads "Running" beside
    # a Start button, and "Shared" gets claimed when nothing shares it -- which
    # is what the page said about a demo while it was being torn down.
    covered = d.get("covered_by") or []

    if live == "starting" or (busy and wanted and live == "partial"):
        # A demo mid-reconcile is PARTIAL by definition: its stacks come up one
        # at a time. Calling that "Incomplete" in alarm red -- which it did --
        # tells the room something is wrong at the exact moment everything is
        # going right.
        state, badge = "Starting", "/status/badge-warn"
    elif live == "unhealthy":
        state, badge = "Unhealthy", "/status/badge-alarm"
    elif wanted and live == "running" and _still_coming_up(d, raw):
        # Containers up is not a gateway serving (see "A stack started is not
        # a stack ready", DEMO-CONSOLE.md). The card and every demo tab's rail
        # read this word, and "Running" over a banner saying "not ready yet:
        # ignition-edge2 is up but not serving yet" was the one inconsistency a
        # first-time reviewer found across the four tabs (21/09/2026).
        state, badge = "Starting", "/status/badge-warn"
    elif wanted and live == "running":
        state, badge = "Running", "/status/badge-ok"
    elif wanted and live == "partial":
        state, badge = "Incomplete", "/status/badge-alarm"
    elif live in ("running", "partial") and covered:
        state, badge = "Shared", "/status/badge-neutral"
    elif live in ("running", "partial"):
        # Up, and nothing wants it: started by hand, or left behind. Saying
        # "Running" here invites you to walk into a demonstration relying on
        # something the console will stop the next time it reconciles.
        state, badge = "Up anyway", "/status/badge-neutral"
    else:
        state, badge = "Stopped", "/status/badge-neutral"

    return {
        # Always present, never true here. DemoTile picks its view with
        # `if({view.params.demo.placeholder}, 'DemoSlot', 'DemoCard')`, and an
        # expression reading a key that is absent on some instances and present
        # on others is a difference the page would render rather than report.
        "placeholder": False,
        "id": d.get("id"),
        "title": d.get("title"),
        "blurb": d.get("blurb", ""),
        "page": d.get("page", ""),
        # Which tab this demo is watched on. The CARD no longer prints it --
        # every demo is now titled after its own tab ("EAM Demo"), so the line
        # saying "Watch it on the EAM tab" was the title again in smaller
        # type. The field still matters: it is what disables that tab until
        # this demo is running.
        "tab": d.get("tab", ""),
        "wanted": wanted,
        "live": live,
        "state": state,
        "badge": badge,
        "action": "Stop" if wanted else "Start",
        # The one binding shape, per card -- see demo_action. `can.toggle`
        # decides whether the button is live and what it says instead.
        "can": {"toggle": _toggle_can(d, busy, raw,
                                      "Stop" if wanted else "Start",
                                      d.get("title") or d.get("id"))},
        # STOP WAS `/buttons/chip`, AND THAT IS THE SAME CLASS THE GATEWAY
        # LINKS USE -- a translucent pill. So the one destructive control on
        # the card was drawn identically to "Gateway UI" beside it, and read
        # as another thing to open rather than the thing that tears the demo
        # down. `/buttons/danger` is in every theme, and it keeps Start's
        # shape and weight (8px radius, same padding) while being red, so the
        # pair still reads as one control in two states.
        "action_class": "/buttons/danger" if wanted else "/buttons/primary",
        # The glyph belongs beside the class rather than in a view expression,
        # for the same reason `action` and `action_class` already do: one
        # place decides what this button IS, and the view only renders it.
        "action_icon": "material/stop" if wanted else "material/play_arrow",
        "stacks_line": _stacks_line(stacks, up, total),
        "cost_line": _cost_line(d),
        "links": _links(stacks),
    }


def _still_coming_up(d, raw):
    """Is a running demo's only problem that a gateway has not finished
    starting? Only those words: a failed check on a demo that WAS serving is
    not "Starting", and nor is a verdict being re-checked (its stale answer
    stays up while the new one runs, so it does not read as not-ready)."""
    try:
        ready, why = _readiness_of(d, raw or {})
    except Exception:
        return False
    return (not ready) and ("still starting" in why or "not serving yet" in why)


def _size(n):
    """Bytes as one short number. One decimal place at GB, none below."""
    if n >= 1024 ** 3:
        return "%.1f GB" % (n / float(1024 ** 3))
    return "%d MB" % (n / float(1024 ** 2))


def _cost_line(d):
    """What this demo costs in RAM, measured on this machine.

    wd-control samples every stack while it runs and remembers the figure, so a
    STOPPED demo -- the one you are deciding about -- still has a real number
    rather than a guess typed into a file. A stack that has never run here has
    no figure and the line says so: the total is then a floor, and rounding that
    off to a confident number is how a number stops being believed.
    """
    total = d.get("mem") or 0
    unknown = d.get("mem_unknown") or 0
    if not total:
        return "not measured yet" if unknown else ""
    if unknown:
        # ">=", not "~ ... + 3 never run". Store & forward's one measured stack
        # is Postgres at 29 MB, and a card reading "~29 MB" for a demo that also
        # starts two Ignition gateways is off by a factor of fifty in the one
        # direction a RAM figure must never be wrong in.
        return ">= " + _size(total) + ", " + str(unknown) + " never run"
    return "~" + _size(total)


def _stacks_line(stacks, up, total):
    """What this demo costs, and -- only when it matters -- what is missing.

    A stopped demo annotating every stack with "(down)" says the same word five
    times to tell you what the badge already said. The per-stack detail earns
    its place only when SOME of them are up, which is the state where knowing
    which ones is the whole question.
    """
    names = [s.get("stack", "?") for s in stacks]
    if up == 0:
        return "needs " + str(total) + ": " + ", ".join(names)
    if up == total:
        starting = [s.get("stack") for s in stacks if s.get("health") == "starting"]
        if starting:
            return "still starting: " + ", ".join(starting)
        return "running: " + ", ".join(names)
    detail = []
    for s in stacks:
        name = s.get("stack", "?")
        if not s.get("up"):
            name += " (down)"
        elif s.get("health") == "starting":
            name += " (starting)"
        detail.append(name)
    return str(up) + " of " + str(total) + " up: " + ", ".join(detail)


def _budget_line(host, running_cost, running):
    """RAM with a DENOMINATOR, which is the whole point of this line.

    Every figure on this page used to be a numerator. "~2.3 GB" is unremarkable
    on a 32 GB workstation and impossible on the 8 GB laptop somebody actually
    demonstrates from, and the page could not tell them apart -- so the cost of
    a demo was visible while whether it FITS was not.

    This stack shares its host: on the Windows machine it runs beside the Linux
    VM, and starting everything once drove that host into swap and took the VM
    down with it. Nothing warned anybody, because nothing on screen knew how
    big the machine was.

    ON DOCKER DESKTOP THIS IS NOT THE MACHINE, AND SAYING SO MATTERS. The
    figure is Docker's own allowance; Windows itself has less, and Docker grows
    into it. Measured 22/08/2026 on the work machine: this line read 23.7 GB
    free while Windows had 4.4 GB free -- the reassuring number and the
    dangerous one, at the same moment, and it was the reassuring one on screen.
    We cannot see the host from in here, so the honest answer is to name whose
    memory it is and say the host has less, rather than print a figure the
    reader will take for the machine.
    """
    total = host.get("total") or 0
    if not total:
        return ""                      # no denominator -- say nothing, don't guess
    avail = host.get("available")
    used = host.get("used")
    line = ("Docker's share: " if host.get("docker_desktop")
            else "Machine: ") + _size(total) + " RAM"
    if used is not None:
        line += ", " + _size(used) + " in use"
    if avail is not None:
        line += ", " + _size(avail) + " free"
    mine = (running_cost or {}).get("mem") or 0
    if mine:
        line += "  --  this stack is " + _size(mine) + " of it"
    if host.get("docker_desktop"):
        line += ".  The host has less than this and Docker grows into it."
    return line


def _mem_bar(host, running_cost):
    """The RAM sentence as a PICTURE, plus the words it cannot carry.

    `_budget_line` below says the same thing in prose and is kept for the
    tooltip, because the reason the denominator exists (see its docstring) is a
    paragraph, not a bar. What the bar adds is the one question the sentence
    answered slowly: is there room to start another demo. Two segments -- what
    this stack is using, then everything else in use -- so "my share" and "the
    machine's share" are distinguishable rather than one blended number.

    Percentages are ints because they are widths. A float lands in the style as
    "12.700000000000001%" and Perspective passes it through to CSS.
    """
    total = host.get("total") or 0
    if not total:
        # No denominator. A bar with no total is a bar that means nothing, and
        # a full-looking one would be worse than none -- so the view hides it.
        return {"known": False, "label": "", "mine_pct": 0, "other_pct": 0,
                "free": "", "tight": False}
    used = host.get("used") or 0
    mine = (running_cost or {}).get("mem") or 0
    if mine > used:
        mine = used                      # sampling skew; never draw past 100%
    mine_pct = int(round(100.0 * mine / total))
    other_pct = int(round(100.0 * (used - mine) / total))
    avail = host.get("available")
    return {
        "known": True,
        # "Docker's share" rather than "Machine" on Docker Desktop, for the
        # reason _budget_line spells out: the figure is Docker's allowance and
        # the host has less. The distinction has been the difference between a
        # reassuring number and a true one.
        "label": ("Docker " if host.get("docker_desktop") else "") +
                 _size(used) + " / " + _size(total),
        "mine": _size(mine) if mine else "",
        "mine_pct": mine_pct,
        "other_pct": other_pct,
        "free": _size(avail) + " free" if avail is not None else "",
        # Under one Ignition gateway's worth left. The bar turns warn, which is
        # the one state where the colour is doing work rather than decoration.
        "tight": avail is not None and avail < 2 * 1024 ** 3,
    }


def _secs(n):
    """Seconds as one short human span. Under two minutes stays in seconds,
    because "1m 47s" is harder to compare against "about 95s" than "107s" is."""
    n = int(n)
    if n < 120:
        return str(n) + "s"
    return str(n // 60) + "m " + str(n % 60) + "s"


def _progress(job, now):
    """How far into a start we are -- elapsed, an estimate, and a bar.

    WHY THIS EXISTS. A demo takes one to three minutes to come up, and until now
    the page said only "Starting". A start going perfectly was indistinguishable
    from one that had wedged, at the exact moment somebody is standing in front
    of a customer deciding whether to reload -- and reloading mid-reconcile is
    how you end up clicking Start again and getting "busy".

    Elapsed alone answers "is anything happening" but not "should I be worried".
    So wd-control remembers how long each stack has taken to become healthy on
    THIS machine and the estimate is the sum over the plan; see TIMING in
    control/service.py.

    THE ESTIMATE MUST BE ALLOWED TO BE WRONG, OUT LOUD. Once elapsed passes it,
    the line stops counting down and says so -- "112s, longer than the usual
    ~95s". A countdown that hits zero and sits there is worse than no countdown:
    it converts "this is taking a while" into "this is stuck", which is the
    wrong conclusion and the expensive one.

    And it says "about". A stack never started here contributes nothing to the
    sum, so the estimate is a FLOOR, not a prediction -- the same reason a demo
    card prints ">= 2.1 GB, 3 never run" rather than a confident total.
    """
    if job.get("state") != "working":
        return {"working": False, "text": "", "pct": 0, "step": ""}

    started = job.get("started") or 0
    elapsed = max(0, int(now) - int(started)) if started else 0
    eta = int(job.get("eta") or 0)
    unmeasured = int(job.get("unmeasured") or 0)
    plan = job.get("plan") or []
    step = int(job.get("step") or 0)

    what = str(job.get("what", "working"))
    about = "about " if unmeasured == 0 else "at least "

    if eta and elapsed < eta:
        text = (what + " -- " + _secs(elapsed) + " of " + about + _secs(eta)
                + ", " + _secs(eta - elapsed) + " to go")
    elif eta:
        text = (what + " -- " + _secs(elapsed) + ", longer than the usual "
                + _secs(eta))
    else:
        # Nothing measured yet on this machine. Elapsed alone, and no invented
        # denominator: a made-up estimate that is wrong the first time is how a
        # progress indicator stops being believed.
        text = what + " -- " + _secs(elapsed) + " so far"

    # The bar moves on TIME where there is an estimate, because a bar that sits
    # still for the ninety seconds one gateway takes is the complaint this is
    # answering. Capped below 100 so it cannot claim to be finished while the
    # job is still running -- the step count is what says that.
    if eta:
        pct = min(96, int(100.0 * elapsed / eta))
    else:
        pct = int(100.0 * step / len(plan)) if plan else 0
    # Never go backwards past what is actually done.
    if plan:
        pct = max(pct, int(100.0 * step / len(plan)))

    return {
        "working": True,
        "text": text,
        "pct": pct,
        # COMPLETED of total, said as "done". Phrased as "step 2 of 2" it read
        # as finished while the job was still working -- the last stack is the
        # one being waited on, so the number people would take for progress was
        # exactly one ahead of the truth.
        "step": (str(step) + " of " + str(len(plan)) + " done") if plan else "",
    }


# How many problem rows the readiness panel shows before it counts the rest.
READY_ROWS = 2


def _readiness(raw, demos, now):
    """Would the started demos actually WORK if you clicked through them now.

    The answer comes from `scripts/verify-demos.sh --json`, run by wd-control and
    cached -- see the Readiness section of control/service.py for why it is a
    cached snapshot with a button rather than a live reading. Nothing here
    re-implements a check; the terminal and this page get the same verdict from
    the same script, which is the rule stack.sh already sets for "bring a stack
    up".

    A SNAPSHOT MUST SAY HOW OLD IT IS. A readiness panel that looks live and is
    twenty minutes stale is worse than none: it is the same failure as counting
    stored history rows and calling the road healthy, one level up. So the age
    is always on screen and "never" is a state, not a blank.

    ONE LINE WHEN GREEN, the detail only when there is detail. That is the
    "both audiences" split: the rig strip above is the always-on customer-facing
    half, and this expands into repair commands only when something is actually
    wrong -- so nothing red is drawn unless something is red.
    """
    snap = raw.get("verify") or {}
    result = snap.get("result")
    titles = dict((d.get("id"), d.get("title")) for d in demos)
    titles["core"] = "Core"

    at = snap.get("at") or 0
    if not at:
        age = "never checked"
    else:
        secs = max(0, int(now) - int(at))
        if secs < 90:
            age = "checked " + str(secs) + "s ago"
        elif secs < 3600:
            age = "checked " + str(secs // 60) + "m ago"
        else:
            age = "checked " + str(secs // 3600) + "h ago"

    if snap.get("running"):
        return {"known": True, "running": True, "ok": True, "age": "checking...",
                "headline": "checking readiness -- this takes a few seconds",
                "rows": []}

    if not result:
        return {"known": False, "running": False, "ok": True, "age": age,
                "headline": "readiness has not been checked on this machine yet",
                "rows": []}
    if result.get("error"):
        return {"known": True, "running": False, "ok": False, "age": age,
                "headline": str(result.get("error")), "rows": []}

    sections = result.get("sections") or []
    failed = int(result.get("failed") or 0)
    checked = sum(int(s.get("checked") or 0) for s in sections)
    wanted = result.get("wanted") or []

    rows = []
    for s in sections:
        if s.get("ok"):
            continue
        for p in s.get("problems") or []:
            rows.append({
                "demo": titles.get(s.get("demo"), s.get("demo")),
                "what": p.get("what", ""),
                "fix": p.get("fix", ""),
            })

    # TWO ROWS, THEN A COUNT. Readiness now runs on its own after every
    # reconcile, so the panel is populated rather than "never checked" -- and
    # four problem rows pushed the demo tiles below the fold at 1366x640, the
    # size every screen here has to fit. The rest are one command away.
    if len(rows) > READY_ROWS:
        more = len(rows) - READY_ROWS
        rows = rows[:READY_ROWS] + [{
            "demo": "More",
            "what": "%d more problem%s" % (more, "" if more == 1 else "s"),
            "fix": "./wd -- scripts/verify-demos.sh lists them all"}]

    if failed:
        headline = (str(failed) + " problem" + ("" if failed == 1 else "s")
                    + " of " + str(checked) + " checks")
    elif not wanted:
        # Scope, said out loud. A green tick on a core-only machine says nothing
        # about the three demonstrations, and printing "everything is ready"
        # there would be a lie about what was looked at -- the same lie
        # verify-demos refuses to print in the terminal.
        headline = ("core ready -- " + str(checked)
                    + " checks. No demo started, so none was checked")
    else:
        headline = ("ready: " + ", ".join(titles.get(w, w) for w in wanted)
                    + " -- " + str(checked) + " checks")

    return {"known": True, "running": False, "ok": failed == 0, "age": age,
            "headline": headline, "rows": rows}


def _load_pill(host):
    """CPU, as load per core rather than a percentage -- see host_load() in
    control/service.py for why that choice is deliberate.

    Warns above 1.5 per core. Not 1.0: this stack's normal state includes a
    gateway starting, which is legitimately CPU-bound for about a minute, and a
    pill that goes amber every time you start a demo is a pill nobody reads by
    the third demonstration. Above 1.5 sustained is the shape of the failure
    that marked a healthy broker unhealthy.
    """
    per = host.get("load_per_core")
    if per is None:
        return {"known": False, "text": "", "high": False}
    return {
        "known": True,
        "text": "load " + str(per),
        "high": per > 1.5,
    }


def _rig(raw, core_names):
    """Every stack this repo owns, with its health -- the always-populated half
    of the page.

    This is what replaced 112px of prose. "Core only -- nothing else is running"
    is a sentence describing a picture: three lit chips and six grey ones say it
    faster and also say WHICH, which the sentence never did.

    Built from the union of core and every demo's stacks, because that is the
    set `make validate` already guarantees is complete -- it fails on a stack in
    no demo and not core, so nothing can be missing from this row without CI
    saying so first.
    """
    seen, out = set(), []
    groups = [("core", (raw.get("core") or {}).get("stacks") or [])]
    for demo in raw.get("demos") or []:
        groups.append((demo.get("id") or "", demo.get("stacks") or []))
    for owner, stacks in groups:
        for s in stacks:
            name = s.get("stack") or "?"
            if name in seen:
                continue
            seen.add(name)
            health = s.get("health") or "down"
            up = bool(s.get("up"))
            mem = s.get("mem")
            out.append({
                "stack": name,
                "health": health,
                "up": up,
                "core": name in core_names,
                # A dot class rather than a word. Nine chips each spelling
                # "healthy" is a paragraph again.
                "dot": {"healthy": "/status/badge-ok",
                        "starting": "/status/badge-warn",
                        "unhealthy": "/status/badge-alarm"}.get(
                            health, "/status/badge-neutral"),
                # ONLY WHILE UP. `mem` is the LAST MEASURED figure and survives
                # the stack stopping, deliberately -- that is what lets a
                # stopped demo's card print a real RAM cost instead of a guess.
                # On a rig chip the same number reads as "using 1.0 GB right
                # now", so a machine with everything stopped listed 6 GB of
                # phantom usage. A cost is not a reading.
                "mem": _size(mem) if (mem and up) else "",
                # Down chips have to LOOK down. Nine identically-styled pills
                # differing only by an 8px dot read as nine healthy stacks at a
                # glance, which is the one thing this row exists to prevent.
                "dim": not up,
                "owner": owner,
            })
    return out


def _trial_line():
    """The hub's own trial, in minutes. In-process and free -- gw_trial reads
    the licence manager inside this JVM, no HTTP and no credentials.

    Only the hub: an edge's trial needs that edge's session cookie, which is
    the whole reason the EAM tab's button reports rather than resets. A number
    here that silently meant "the hub" while looking like "the rig" would be
    worse than no number, so it is labelled.
    """
    try:
        import gw_trial
        left = gw_trial.seconds_left()
    except (Exception, JThrowable):
        return {"known": False, "text": "", "low": False}
    if left is None or left <= 0:
        return {"known": True, "text": "hub trial EXPIRED", "low": True}
    mins = int(left / 60)
    return {"known": True, "text": "hub trial " + str(mins) + "m",
            # verify-demos fails under 30 minutes, so the page warns on the
            # same threshold rather than inventing a second opinion.
            "low": mins < 30}


def trial_line():
    """The hub's trial, for the console HEADER.

    It lived on the Demos tab only. An unlicensed gateway runs Perspective on a
    rolling two-hour trial, so sitting on the MQTT or Redundancy tab when
    it lapsed, the session simply died with nothing on screen having said the
    clock was running. The header is above every tab, which is where a clock
    that can end the session belongs.
    """
    return _trial_line()


def _version(raw):
    """Which Ignition build the gateways run -- one chip on the rig strip.

    THE CHIP HAS TO SAY WHICH OF TWO NUMBERS IT IS, because they differ
    exactly when it matters. Writing a version into a stack's .env changes
    nothing until the container is recreated, so a chip showing only the
    setting would report a switch as done the moment it was chosen -- and the
    gateway would still be on the old build, which is the state you would be
    demonstrating from.

    Three states, and the middle one is the reason this exists:

        8.3.8                   set and running -- nothing to do
        8.3.8 -> 8.3.9          chosen, NOT applied: needs down && up
        MIXED                   the gateways disagree, which stops a redundant
                                pair syncing and makes an EAM push a gamble

    A gateway that is DOWN is never "pending". It is stopped, which is this
    rig's normal resting state, and a chip permanently asking for a restart
    on a deliberately-stopped stack is a chip people stop reading.
    """
    v = raw.get("ignition") or {}
    if not v.get("agree"):
        return {"known": True, "text": "Ignition MIXED", "warn": True,
                "tip": "the gateways are set to different versions -- a "
                       "redundant pair on two builds does not sync. Fix with: "
                       "make ignition-version SET=<version>"}
    eff = v.get("effective") or ""
    if not eff:
        return {"known": False, "text": "", "warn": False, "tip": ""}
    pending = v.get("pending") or []
    if pending:
        run = v.get("running") or "?"
        return {"known": True, "text": "Ignition " + run + " -> " + eff,
                "warn": True,
                "tip": ("chosen but not applied -- " + ", ".join(pending)
                        + " still runs what it was created from. Take it with:"
                          " ./wd down && ./wd up")}
    return {"known": True, "text": "Ignition " + eff, "warn": False,
            "tip": "modules are pinned to Ignition "
                   + (v.get("module_series") or "?")
                   + " -- a patch inside that series needs no module work"}


def _stack(raw):
    """Which release this machine is on, and whether anything here has been
    changed since -- because an update overwrites it.

    Same contract as _version(): every key always present and never a bare
    error, because an unbound sub-path renders a component's error box. The
    Ignition version is `_version`'s and is NOT overloaded here: they are two
    different numbers that differ exactly when it matters.

    `newer` and `behind` ride along for the Demos page's "newer releases"
    block, which lists what an update would bring.
    """
    v = (raw or {}).get("version") or {}
    ver = v.get("version") or ""
    if not ver or ver in ("unknown", "unreleased"):
        return {"known": False, "text": "", "warn": False, "tip": "",
                "behind": 0, "newer": []}

    modified = bool(v.get("modified"))
    behind = int(v.get("behind") or 0)
    latest = v.get("latest") or ""

    # NO "+7". The commits-past-the-tag count is on /state and is deliberately
    # not shown: the only thing the chip is for is which release this is, and
    # whether an update would overwrite something somebody did.
    text = "stack " + ver
    if modified:
        text += u" \u00b7 modified"
    if behind > 0 and latest:
        text += u" \u2192 " + latest

    bits = []
    if modified:
        bits.append("CHANGED since this release: "
                    + "; ".join(v.get("modifiedWhy") or []) + ".")
        bits.append("An update overwrites these. 'make snapshot' keeps a copy "
                    "first; 'make mqtt-reset' is the documented way back for "
                    "the MQTT demo.")
    if not v.get("driftAt"):
        bits.append("Gateway settings have never been checked on this machine "
                    "-- run 'make drift'.")
    if behind > 0 and latest:
        bits.append("%d newer release(s) waiting: %s. Take them on the HOST: "
                    "make update." % (behind, ", ".join(
                        "%s (%s)" % (x.get("version", ""), x.get("summary", ""))
                        for x in (v.get("newer") or [])[:4])))
    if not bits:
        bits.append("On release %s, unchanged, with nothing newer waiting." % ver)

    newer = [{"version": str(x.get("version", "")),
              "summary": str(x.get("summary", ""))}
             for x in (v.get("newer") or [])]
    return {"known": True, "text": text,
            "warn": modified or behind > 0, "tip": " ".join(bits),
            "behind": behind if newer else 0, "newer": newer}


def _core_line(core, running):
    stacks = core.get("stacks") or []
    names = ", ".join([s.get("stack", "?") for s in stacks])
    line = ("Core " + str(core.get("up", 0)) + "/" + str(core.get("count", 0))
            + " up: " + names)
    if core.get("up") != core.get("count"):
        line += "  -- something in the core is DOWN"

    # The two numbers people actually want: what the machine is costing now, and
    # how much of that is the part they cannot switch off. On a core-only
    # machine they are the same figure, so it is only printed once.
    #
    # A stack that is up but has not been measured yet -- anything in its first
    # 150 seconds -- would otherwise make the total quietly too small, which is
    # the one direction a RAM figure must never be wrong in.
    core_mem = core.get("mem") or 0
    all_mem = running.get("mem") or 0
    pending = " (still measuring)" if running.get("mem_unknown") else ""
    if all_mem and all_mem > core_mem:
        line += ("   ~" + _size(all_mem) + " up, of which ~" + _size(core_mem)
                 + " is core" + pending)
    elif core_mem:
        line += "   ~" + _size(core_mem) + pending
    return line


# How long a refused click stays on screen. Long enough to read, short enough
# that it cannot be mistaken for a description of what is happening now.
REFUSAL_SECONDS = 12


def _job_line(job, refused, now):
    state = job.get("state", "idle")
    log = job.get("log") or []
    last = log[-1] if log else ""

    if state == "working":
        line = "Working: " + str(job.get("what", "")) + "   " + last
    elif state == "failed":
        line = "FAILED: " + str(job.get("what", "")) + "   " + last
    elif state == "idle":
        line = ""
    else:
        line = "Last: " + str(job.get("what", "")) + " -- done"

    # A click that was turned away, reported from the CONTROL PLANE rather than
    # from the click's own reply. A second click during a reconcile is common --
    # a gateway takes a minute and people click again -- and it used to vanish
    # silently, because the button that was accepted overwrote the message from
    # the button that was refused. Told nothing, you click a third time.
    at = refused.get("at") or 0
    if at and now and 0 <= (now - at) <= REFUSAL_SECONDS:
        ignored = "Ignored: " + str(refused.get("what", "that click")) + " -- one at a time"
        line = ignored + ("   " + line if line else "")
    return line


# The tiles are ONE ROW of four: the demos, then inert dotted slots filling out
# the row. Slots say more demonstrations are coming; a whole reserved SECOND row
# of them said it at the cost of a third of the page, which is what the six-tile
# frame did and why it is gone.
#
# FOUR IS A LAYOUT NUMBER AND IT LIVES IN TWO PLACES. The other half is the
# `demos` repeater's `elementPosition.basis` of 24% in Demos/view.json. Change
# one without the other and the row silently gains or loses a column.
#
# Derived, not fixed: pad up to the next multiple of ACROSS, so the tiles always
# fill whole rows and never reserve an empty one. Three demos is 3 + 1 slot;
# four is four cards and no slot at all.
ACROSS = 4


def _padded(demos):
    """Fill out the last row with slots. Called LAST, after _headline and
    _detail have had the real list: both index d["live"], d["state"] or
    d["id"] unguarded, so a placeholder reaching them is a KeyError on the
    whole page rather than one odd card."""
    # `(-0) % 4` is 0, so an EMPTY list would round up to nothing and the frame
    # would vanish -- which is exactly the control-plane-down branch below, the
    # one case where the page most needs to still look like itself.
    short = ACROSS - len(demos) % ACROSS if len(demos) % ACROSS else 0
    if not demos:
        short = ACROSS
    return demos + [{"placeholder": True} for _ in range(short)]


def _headline(running, job):
    if job.get("state") == "working":
        return "Reconciling: " + str(job.get("what", ""))
    if not running:
        return "Core only -- nothing else is running"
    return ", ".join([d["title"] for d in running])


def _detail(demos):
    """Only the part that is NEWS.

    This used to open with "Start what you are about to show; stop it when you
    are done. The core stays up either way." -- two sentences of instruction,
    on screen permanently, costing a line of a page whose top third was prose.
    Instruction belongs in the Guide button, which is three feet away in the
    header and is where demo_guide.TOPICS already lives.

    What is left is the half that changes: a demo showing "Shared" is up only
    because a neighbour needs its stacks, and that is genuinely surprising the
    first time. Empty otherwise, so the view's meta.visible binding removes the
    row rather than reserving it.
    """
    shared = [d["id"] for d in demos if d["state"] == "Shared"]
    if not shared:
        return ""
    return ("Stacks are shared, so " + ", ".join(shared)
            + " is partly up already for something else.")


# ------------------------------------------------------------------- actions
def start(demo_id):
    return _act("/demos/" + demo_id + "/start", "starting " + demo_id)


def stop(demo_id):
    return _act("/demos/" + demo_id + "/stop", "stopping " + demo_id)


def stop_all():
    return _act("/stop-all", "stopping every demo")


def toggle(demo_id, wanted):
    """One button per card. What it does depends on what the card says it is
    doing, so there is never a Start beside a Stop for the same thing."""
    if wanted:
        return stop(demo_id)
    return start(demo_id)


def _act(path, what):
    result = _call("POST", path, ACTION_TIMEOUT)
    # A REFUSAL IS NOT A FAILURE TO REACH ANYTHING. 409 is the control plane
    # saying "one at a time", and it arrives as a non-2xx -- so this reported
    # "could not reach the demo control plane" for a click that was heard,
    # understood and turned away. The buttons are latched now, so this branch
    # should be unreachable from the page; it is the one that has to be right
    # if a click is already in the air when a job starts.
    if result is not None and result.get("status") == 409:
        return "something else is running just now -- one reconcile at a time"
    if result is None or "error" in result:
        return "could not reach the demo control plane"
    if not result.get("ok"):
        # 409: another reconcile is already running. Saying so is the whole
        # point -- a second click that silently does nothing is why people click
        # a third time.
        return "busy -- " + str(result.get("error", "something else is running"))
    return what


def check_readiness():
    """Ask wd-control to run verify-demos. Returns the line to show at once.

    202 and return, like every other action here: a readiness run shells into
    every gateway and takes tens of seconds, and the page is already polling
    /state, so blocking the click would give a frozen button for the same
    information arriving later.
    """
    out = _call("POST", "/verify", ACTION_TIMEOUT)
    if out is None or (isinstance(out, dict) and out.get("error")):
        return "could not start the readiness check: " + str(
            (out or {}).get("error", "wd-control did not answer"))
    # NOTHING on success, and that is the point. The status line is a permanent
    # band -- it holds whatever an action last returned until another action
    # replaces it -- while the readiness panel four rows above already says
    # "checking readiness -- this takes a few seconds" and then replaces itself
    # with the verdict. So a status line here is the same sentence twice while
    # the check runs, and a lie afterwards: measured on the work machine
    # 25/08/2026, "checking readiness..." was still on screen beside a finished
    # result reading "1 problem of 23 checks, checked 38s ago".
    #
    # The rule this earns: an action may only write a status line for something
    # NOTHING ELSE ON THE PAGE REPORTS. Start and stop still do -- their own
    # progress row is gated on a job being in flight and says nothing about a
    # refusal -- but anything with a panel of its own must stay quiet and let
    # the panel speak, or the page carries two accounts of one thing and only
    # one of them is kept up to date.
    return ""

# --- the Ignition version picker ---------------------------------------------

def version_picker():
    """What the picker popup renders: where we are, and what may be chosen.

    THE LIST IS BUILT BY THE CONTROL PLANE, not here, and it is deliberately
    narrower than "every tag Docker Hub has". It offers only the module series
    and only builds no older than these volumes have run, because a picker that
    can offer something the script will refuse teaches people the buttons lie.
    The script still checks all of it -- this half keeps the offer honest, the
    guards keep it safe.
    """
    raw = _state()
    if raw is None or "error" in raw:
        import demo_action
        return {"ok": False, "note": "the control plane is not answering",
                "current": "", "running": "", "rows": [], "backup": "",
                "pending": "", "warn": "",
                "can": {"apply": demo_action.can(
                    False, "the control plane is not answering")}}
    v = raw.get("ignition") or {}
    cur = v.get("effective") or ""
    run = v.get("running") or ""
    rows = []
    for x in v.get("available") or []:
        rows.append({
            "version": x,
            "current": x == cur,
            # The label carries the meaning, not a colour: "current" and
            # "newer" are different offers and a row that only differed by
            # shade would need a legend nobody reads.
            "note": "in use" if x == cur else ("running" if x == run else ""),
        })
    note = ""
    if not v.get("available_checked"):
        note = "checking Docker Hub for available builds..."
    elif v.get("available_error"):
        note = "could not reach Docker Hub -- showing what is set"
    elif len(rows) <= 1:
        note = "nothing newer in the " + (v.get("module_series") or "?") + " series"

    at = v.get("backup_at") or 0
    if not at:
        backup = "no backup on this machine"
    else:
        age = int(system.date.toMillis(system.date.now()) / 1000) - at
        if age < 3600:
            backup = "last backup " + str(int(age / 60)) + "m ago"
        elif age < 172800:
            backup = "last backup " + str(int(age / 3600)) + "h ago"
        else:
            backup = "last backup " + str(int(age / 86400)) + "d ago"

    pending = ", ".join(v.get("pending") or [])
    return {
        "ok": True, "note": note, "current": cur, "running": run,
        "rows": rows, "backup": backup, "pending": pending,
        "can": {"apply": _version_can(raw, cur)},
        "warn": ("Switching recreates the gateways that are RUNNING -- this page "
                 "goes away for about a minute and comes back on the new build. "
                 "It is FORWARD ONLY: a gateway upgrades its configuration store "
                 "on first start and the older build cannot read it back."),
    }


def _version_can(raw, current):
    """The switch button. A version switch recreates every running gateway, so
    it is refused for exactly as long as a reconcile is in flight -- which the
    control plane already does with a 409, and the picker's own docstring
    already says a picker that offers what the script will refuse teaches
    people the buttons lie. The reply broke the same rule."""
    import demo_action

    job = raw.get("job") or {}
    if job.get("state") == "working":
        return demo_action.can(
            False, "the console is %s just now -- the gateways cannot be "
                   "recreated in the middle of it" % str(job.get("what") or "busy"))
    return demo_action.can(True)


def set_version(version):
    """Ask the control plane to switch. Returns a line for the page.

    The click only STARTS the job; the page watches it through /state like any
    other. That is not politeness about spinners -- this recreates the gateway
    serving this very session, so a handler that waited for the result would be
    waiting inside the web server it is tearing down. The job runs in
    wd-control, which is a different container, so its log outlives the gateway
    going away and is there to read when the session reconnects.
    """
    if not version:
        return "pick a version first"
    r = _call("POST", "/ignition-version/" + str(version), ACTION_TIMEOUT)
    # THE REFUSALS, IN WORDS. Both of these used to reach the page as the
    # literal string `wd-control returned HTTP 409` / `... HTTP 400`, which is
    # a status code where a sentence belongs -- on the one popup whose own
    # docstring argues that an offer the script will refuse teaches people the
    # buttons lie.
    status = (r or {}).get("status")
    if status == 409:
        return ("a reconcile is already running -- try again when it has "
                "finished")
    if status == 400:
        return ("that build is no longer on offer -- reopen this popup for the "
                "current list")
    if r is None or "error" in r:
        return (r or {}).get("error", "the control plane is not answering")
    return "switching to Ignition " + str(version) + " -- the gateways restart now"


# --- MQTT cuts: one edge off the broker, on a deadline wd-control keeps ---------
#
# The broker is MQTT Distributor on the hub pair, and it has no per-client kick
# (docs/MQTT-DISTRIBUTOR.md, T-D12). The cut is a network one instead:
# wd-control disconnects the edge from `wd-mqtt`, the network that carries MQTT
# only, and reconnects it when the deadline passes -- a deadline it keeps on its
# own volume, so neither a gateway restart nor its own loses the restore. See
# control/mqttcut.py. These are the only callers that carry a token: the rest
# of wd-control is unauthenticated on backbone (control/service.py says why),
# and a cut is the one action that changes a running demo's network.

CONTROL_KEY_NAME = "wd-control-token"


def _control_token():
    """The token, from the hub's own `wd` secret store (scripts/ign-secrets.sh).
    None when it is missing. Never logged, never returned to a view."""
    try:
        with system.secrets.readSecretValue("wd", CONTROL_KEY_NAME) as plain:
            return str(plain.getSecretAsString())
    except (Exception, JThrowable), e:
        _logger().warn("no %s secret on this gateway: %s" % (CONTROL_KEY_NAME, e))
        return None


def _mqtt_post(verb, body):
    """(ok, reply) from POST /mqtt/<verb>. Never raises: this sits behind a button."""
    token = _control_token()
    if not token:
        return False, {"why": "this gateway has no wd-control token -- run "
                              "scripts/ign-secrets.sh"}
    try:
        client = system.net.httpClient(timeout=ACTION_TIMEOUT, version="HTTP_1_1")
        response = client.post(CONTROL + "/mqtt/" + verb, data=body,
                               headers={"Content-Type": "application/json",
                                        "X-WD-Token": token})
        try:
            reply = response.json or {}
        except (Exception, JThrowable):
            reply = {}
        if not reply.get("why") and not response.good:
            reply = {"why": "wd-control answered HTTP %d" % response.statusCode}
        # The cut is now in /state; drop the memo so the next poll shows it.
        _STATE["value"] = None
        return bool(response.good and reply.get("ok")), reply
    except (Exception, JThrowable), e:
        _logger().warn("wd-control /mqtt/%s failed: %s" % (verb, e))
        return False, {"why": "the control plane (wd-control) is not answering"}


def mqtt_cut(stack, seconds):
    """Cut one edge off the broker for `seconds`. (ok, why-if-not)."""
    ok, reply = _mqtt_post("cut", {"stack": stack, "seconds": int(seconds)})
    return ok, str(reply.get("why", ""))


def mqtt_restore(stack):
    """Put it back now. (ok, why-if-not)."""
    ok, reply = _mqtt_post("restore", {"stack": stack})
    return ok, str(reply.get("why", ""))


def mqtt_cuts():
    """({stack: seconds left}, why-unknown) for every edge wd-control has cut.

    Seconds are worked out here from `until`, not taken from /state's own
    count, which is up to a few seconds old by the time it arrives. An empty
    dict with a reason means NOT KNOWN, which a page must not draw as "nothing
    is cut"."""
    raw = _state()
    if raw is None or "error" in raw:
        return {}, "the control plane (wd-control) is not answering"
    block = raw.get("mqtt")
    if block is None:
        return {}, "wd-control is too old to cut MQTT -- restart it (make up STACK=wd-control)"
    now = system.date.toMillis(system.date.now()) / 1000.0
    out = {}
    for stack, cut in (block.get("cuts") or {}).items():
        left = int(round(float(cut.get("until", 0)) - now))
        if left > 0:
            out[str(stack)] = left
    return out, ""
