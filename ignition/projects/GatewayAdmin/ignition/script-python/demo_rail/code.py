"""One left rail, four demo tabs: its words, its facts and its actions.

WHY ONE MODULE. The four demo tabs grew in batches and each invented its own
rail: EAM and Store & Forward said "CONTROLS" over a column of buttons,
Redundancy added group headings and a banner, MQTT put its scenarios in a row
across the content. The same state was called "Stopped" on a card and "not
running" on a tab, and a fact meaning "is it working" was a card metric on one
tab and a sentence on another. Nothing was wrong on any one page; the four
together read as four products.

So the rail's CONTENT lives here, once:

    rail(<demo id>)   the head, the banner, the facts, the gateway links
    fire(session, k)  every rail button's work, by key

and its SHAPE lives in the six views scripts/gen-rail-views.py writes, which
the three hand-built tabs and the generated MQTT tab all embed.

THE STATE WORDS ARE demo_control's, VERBATIM. `rail()` runs the demo's own
entry from /state through `demo_control._demo`, the same function the Demos
tab's card uses, and shows the `state` and `badge` it returns. A rail with its
own vocabulary would be a second answer to "is this demo running", and the two
would differ on the day one of them was edited.

Jython 2.7: no f-strings, `except Exception, e:`, and `except Exception` does
not catch Java throwables, so anything touching Java names both.
"""
from java.lang import Throwable as JThrowable

LOG = "DemoRail"

# The rail's own title per demo. Not the demo's `title` from demos.json
# ("EAM Demo"), because the rail sets it in small capitals and a rail reading
# "EAM DEMO DEMO" was the first draft of exactly that.
NAMES = {
    "eam": "EAM DEMO",
    "store-forward": "STORE & FORWARD",
    "redundancy": "REDUNDANCY DEMO",
    "sparkplug": "MQTT DEMO",
}


def _logger():
    return system.util.getLogger(LOG)


def _blank(demo_id, why=""):
    """The rail's not-known shape.

    Never omitted and never a bare error: an unbound sub-path renders a
    component's red error box, and the component here is the one that would
    have explained what was wrong.
    """
    return {"head": {"name": NAMES.get(demo_id, "DEMO"),
                     "state": "Not known", "badge": "badge-neutral",
                     "tip": why or "the console cannot reach its "
                                   "control plane"},
            "banner": {"text": ""}, "notReady": "", "state": "unknown",
            "facts": [], "links": []}


def _restart_note(raw, note):
    """The banner while a half restarted from the page comes back.

    wd-control's readiness only sees a gateway "up but not serving", which
    read as something to fix; the half is restarting itself and needs nothing
    (30/09/2026). The recorder marks a changeover that followed a restart
    request, so for four minutes after one, a banner naming that half says so.
    """
    recs = (raw.get("redundancy") or {}).get("changeovers") or []
    if not recs or recs[0].get("requestKind") != "stop":
        return note
    if system.date.toMillis(system.date.now()) - int(recs[0].get("newActiveAt") or 0) > 240000:
        return note
    stack = {"master": "ignition", "backup": "ignition-backup"}.get(recs[0].get("from"))
    if not stack or stack not in note:
        return note
    return "%s is restarting itself after Restart this gateway -- back in about a minute" % stack


def rail(demo_id):
    """Everything the left rail of `demo_id`'s tab renders, in one poll.

    Never raises: it is wired straight to a binding, and a script transform
    that throws renders a red ERROR box and logs nowhere a script can see.
    """
    try:
        return _rail(demo_id)
    except (Exception, JThrowable), e:
        _logger().warn("rail(%s) failed: %s" % (demo_id, e))
        return _blank(demo_id, str(e)[:80])


def _rail(demo_id):
    import demo_control

    raw = demo_control._state()
    if raw is None or "error" in raw:
        return _blank(demo_id)

    entry = None
    for d in raw.get("demos", []):
        if d.get("id") == demo_id:
            entry = d
            break
    if entry is None:
        return _blank(demo_id, "no demo called '%s'" % demo_id)

    busy = (raw.get("job") or {}).get("state") == "working"
    card = demo_control._demo(entry, busy, raw)
    note = demo_control.ready_note(demo_id)
    if demo_id == "redundancy" and note:
        note = _restart_note(raw, note)
    # SHAPED AS THE VIEWS' PARAMETERS, not as loose keys. Perspective's
    # expression language has no object literal, so a view embedded with
    # `props.params` bound can only be handed a dict that already exists --
    # assembling one per param would be four bindings per piece and a fifth
    # place for a key name to be misspelled.
    return {
        "head": {
            "name": NAMES.get(demo_id, str(card.get("title") or "").upper()),
            "state": card["state"],
            # `_demo` returns the whole class path ("/status/badge-ok")
            # because a card builds `{session.custom.style} + badge`. The rail
            # views take the suffix, so the badge name is written once in
            # either spelling.
            "badge": card["badge"].split("/")[-1],
            "tip": card["cost_line"] or card["stacks_line"],
        },
        "banner": {"text": note},
        "notReady": note,
        "state": entry.get("live", "stopped"),
        "facts": _dashed(_facts(demo_id), not note),
        "links": [{"link": x} for x in _links(demo_id, raw, card["links"])],
    }


def _links(demo_id, raw, own):
    """The gateways this demo's picture shows: the hub first, then its own.

    The hub is CORE, so it is on no demo's card -- but it is in every demo's
    picture (EAM's controller, S&F's historian, the redundant pair's master,
    MQTT's cloud), and "open the hub" is the detour a demonstration makes most.
    Three to a line, so a rail's links are one row on every tab. The MQTT
    tab's old Gateways popup also offered the broker's settings page; it is on
    the hub (Services > MQTT Distributor), and a fourth link was a second row
    the MQTT rail's 640px budget does not have.
    """
    import demo_control

    core = [x for x in demo_control._links((raw.get("core") or {}).get("stacks"))
            if x.get("stack") == "ignition"]
    return core + list(own or [])


def _dashed(facts, ready):
    """A demo that is not ready shows "—", not its last reading.

    MQTT Engine keeps the last value it received long after its edge has gone,
    and a stopped demo displaying it reads as half working (DEMO-CONSOLE.md).
    The banner above says why; a fact in a warning colour beside it would be a
    second, louder account of the same thing. `always` marks the few facts that
    are the HUB's own and true whether or not the demo runs.
    """
    out = []
    for f in facts:
        f = dict(f)
        if not ready and not f.pop("always", False):
            f["value"], f["badge"] = u"\u2014", ""
        f.pop("always", None)
        out.append(f)
    return out


# --- the facts ---------------------------------------------------------------
#
# A rail fact is the demo's OWN answer to "is this working" -- not a metric
# somebody had to hand. Three or four per tab, `label value`, never a sentence:
# the sentence is the content column's one line, and the reason a refused
# button gives.
#
# Every one of these reads a snapshot its demo's module already memoises for
# the cards on the same page, so the rail costs no extra round trip and can
# never disagree with the picture beside it.

def _fact(label, value, badge="", tip="", always=False):
    return {"label": label, "value": unicode(value), "badge": badge,
            "tip": tip, "always": always}


def _facts(demo_id):
    try:
        if demo_id == "eam":
            return _eam_facts()
        if demo_id == "store-forward":
            return _sf_facts()
        if demo_id == "redundancy":
            return _red_facts()
        if demo_id == "sparkplug":
            return _mqtt_facts()
    except (Exception, JThrowable), e:
        _logger().warn("facts(%s) failed: %s" % (demo_id, e))
    return []


def _eam_facts():
    import gateway_admin

    rows = gateway_admin.snapshot()
    edges = rows[1:]
    linked = len([r for r in edges if r.get("connected") is True])
    done = len([r for r in edges if r.get("confirmed")])
    last = 0
    for entry in (gateway_admin._read_state() or {}).values():
        at = gateway_admin._push_at(entry)
        if at > last:
            last = at
    return [
        _fact("Edges linked", "%d of %d" % (linked, len(edges)),
              "badge-ok" if linked == len(edges) else "badge-warn",
              "over the Gateway Network"),
        _fact("Theme applied", "%d of %d" % (done, len(edges)),
              "badge-ok" if done == len(edges) and edges else "badge-neutral",
              "read back from each edge, not from our own push record"),
        _fact("Last push", gateway_admin._fmt(last) if last else "never",
              always=True),
        # Not "Theme here": the hub's card beside the rail prints it in 20px.
    ]


def _sf_facts():
    import sf_demo

    d = sf_demo.summary()
    facts = []
    for r in d.get("rows") or []:
        # The strip's words for the same state (sf_demo._words), so the rail
        # and the box beside it do not say STREAMING and Streaming.
        facts.append(_fact(
            "%s (%s)" % (r["label"], r["transportShort"]),
            sf_demo._words(r["state"]),
            {"good": "badge-ok", "bad": "badge-alarm"}.get(r["stateLevel"],
                                                           "badge-warn"),
            r["transportDetail"]))
    facts.append(_fact(
        "Stored to", sf_demo.HISTORY_PROVIDER, always=True,
        tip="every value each edge sent, kept whether or not the link held"))
    return facts


def _red_facts():
    import redundancy_demo

    d = redundancy_demo.summary()
    if not d.get("paired"):
        return [_fact("Pair", "not configured", "badge-warn",
                      "run: make redundancy")]
    story = redundancy_demo.story()
    proof = redundancy_demo.proof()
    linked = bool(d.get("peerConnected"))
    dash = u"—"
    return [
        # The last changeover, as the content column's card measures it
        # (wd-control's recorder): how fast, and how many seconds were lost.
        _fact("Changeover", proof.get("changeText") or dash, "",
              proof.get("changeSub") or ""),
        _fact("Missing",
              (proof.get("missingText") + " s") if proof.get("missingLevel")
              else (proof.get("has") and "counting" or dash),
              {"good": "badge-ok", "bad": "badge-alarm"}.get(
                  proof.get("missingLevel"), ""),
              proof.get("why") or proof.get("missingSub") or ""),
        _fact("This half", "%s, %s" % (d.get("amMaster") and "master"
                                       or "backup",
                                       d.get("amActive") and "in control"
                                       or "standing by"),
              "badge-ok" if d.get("amActive") else "badge-neutral"),
        _fact("Other half", "linked" if linked else "no link",
              "badge-ok" if linked else "badge-alarm",
              "the redundancy link between the two gateways"),
        _fact("In step", "yes" if story.get("syncing") else "no",
              "badge-ok" if story.get("syncing") else "badge-warn",
              story.get("inStepText") or ""),
    ]


def _mqtt_facts():
    """Two facts, not four. Each edge's own rate is on its box in the strip
    beside this rail, and this is the one rail that also carries a list and the
    list's buttons in 640px (see gen-sparkplug-views.build_main)."""
    import sparkplug_demo

    s = sparkplug_demo.page_strip()
    cloud = s.get("cloud") or {}
    return [
        _fact("Edges live", "%s of %s" % (cloud.get("live", "?"),
                                          cloud.get("total", "?")),
              cloud.get("badge") or "badge-neutral"),
        _fact("Messages", cloud.get("rate") or "--", "",
              "what the broker has carried, counted by the wire witness"),
    ]


# --- the actions -------------------------------------------------------------
#
# ONE TABLE, so a rail button and the guard that greys it can never name
# different work. A RailButton carries an action KEY, not a script: a view
# cannot be handed a handler as a parameter, and a copy of the button per
# handler is what the shared view exists to prevent.
#
# Each entry is (what it says while it runs, a callable taking the session).
# The callable is what `demo_action.run` is given, off the handler thread --
# see demo_action for why the latch has to outlive the click to be seen.

def _eam_push(agents):
    def go():
        import gateway_admin
        return gateway_admin.push(agents) if agents else gateway_admin.push()
    return go


def _commit_theme(session):
    """The one action that reads the session before it acts.

    Read the effective pack BEFORE clearing the override: clearing first makes
    session.custom.style fall back to the committed pack, so you would commit
    the very thing you were replacing.
    """
    import demo_styles

    pack = session.custom.style
    session.custom.styleOverride = ''

    def go():
        return demo_styles.commit_pack(pack)
    return "committing " + pack, go


def _sf_cut(target):
    def build(session):
        seconds = session.custom.sfCutSeconds

        def go():
            import sf_demo
            if target is None:
                return sf_demo.break_all(seconds)
            return sf_demo.break_link(target, seconds)
        return ("cutting both edges" if target is None
                else "cutting " + target), go
    return build


def _red_recovery(session):
    # The dropdown writes the value picked here just before firing; its own
    # value follows the master's real setting (redundancy_demo.master_recovery).
    mode = session.custom.redRecovery

    def go():
        import redundancy_demo
        return redundancy_demo.set_recovery(mode)
    return "saving the recovery mode", go


def _mqtt(what, fn, args=()):
    """An MQTT action. Its answer lands on the session note, which is where
    every other rail button's answer already lands -- the scenario panels used
    to keep a `result` label of their own for it, three of them, each a line of
    text on a 640px page saying what the rail could say beside the button."""
    def build(_session):
        def go():
            import sparkplug_demo
            return getattr(sparkplug_demo, fn)(*args)
        return what, go
    return build


def _mqtt_send(edge):
    """Send the setpoint typed in the rail. It is read from the SESSION at the
    moment of the click (custom.mqttSetpoint, bound both ways to the entry
    field): a RailButton cannot reach into a sibling component for it."""
    def build(session):
        value = session.custom.mqttSetpoint

        def go():
            import sparkplug_demo
            return sparkplug_demo.write_setpoint(edge, value)
        return "sending setpoint %s to Edge %d" % (value, edge + 3), go
    return build


def _gateway_admin(what, fn, args=()):
    def build(_session):
        def go():
            import gateway_admin
            return getattr(gateway_admin, fn)(*args)
        return what, go
    return build


def _sf(what, fn, args=()):
    def build(_session):
        def go():
            import sf_demo
            return getattr(sf_demo, fn)(*args)
        return what, go
    return build


def _red(what, fn, args=()):
    """A redundancy action.

    One helper per module rather than a name passed to `__import__`: a project
    script library is not an ordinary Python module here, and a name resolved
    at call time is a name nothing checks until somebody presses the button.
    """
    def build(_session):
        def go():
            import redundancy_demo
            return getattr(redundancy_demo, fn)(*args)
        return what, go
    return build


# A destructive action asks twice: `stack.sh destroy` demands two tokens on the
# command line, and this is the page's equivalent. The value is the warning the
# first click shows.
CONFIRM = {
    "red.stop": ("Click again to restart this gateway. This page goes away for "
                 "about a minute while the peer takes over."),
}

ACTIONS = {
    # --- EAM
    "eam.pushBoth": lambda s: ("pushing both edges", _eam_push(None)),
    "eam.pushE1": lambda s: ("pushing Edge 1",
                             _eam_push(['Ignition-Edge1'])),
    "eam.pushE2": lambda s: ("pushing Edge 2",
                             _eam_push(['Ignition-Edge2'])),
    "eam.resetTrials": _gateway_admin("resetting the trials", "reset_trials"),
    "eam.commitTheme": _commit_theme,
    # --- Store & Forward
    "sf.cut1": _sf_cut("Ignition-Edge1"),
    "sf.cut2": _sf_cut("Ignition-Edge2"),
    "sf.cutBoth": _sf_cut(None),
    "sf.restore": _sf("restoring the links", "restore_all"),
    # --- Redundancy
    "red.failover": _red("handing responsibility over", "failover"),
    "red.resync": _red("forcing a full sync", "resync"),
    "red.recovery": _red_recovery,
    "red.stop": _red("stopping this gateway", "stop_active"),
    # --- MQTT, keyed by scenario ("mqtt.<scenario>.<verb><edge>") so each
    # scenario's rail block can show its own last answer and nobody else's.
    # The key is also the RailButton's latch key, and the verb+edge part is the
    # `can` entry sparkplug_demo.ctl() returns for that button.
    "mqtt.alarm.trip0": _mqtt("tripping Edge 3's fault", "trip_fault", (0,)),
    "mqtt.alarm.trip1": _mqtt("tripping Edge 4's fault", "trip_fault", (1,)),
    "mqtt.alarm.reset0": _mqtt("clearing Edge 3's fault", "reset_fault", (0,)),
    "mqtt.alarm.reset1": _mqtt("clearing Edge 4's fault", "reset_fault", (1,)),
    "mqtt.alarm.ackEdge0": _mqtt("acknowledging at Edge 3", "ack_edge",
                                 (0, 'all')),
    "mqtt.alarm.ackEdge1": _mqtt("acknowledging at Edge 4", "ack_edge",
                                 (1, 'all')),
    "mqtt.alarm.ackCloud0": _mqtt("acknowledging Edge 3 in the cloud",
                                  "ack_cloud_all", (0,)),
    "mqtt.alarm.ackCloud1": _mqtt("acknowledging Edge 4 in the cloud",
                                  "ack_cloud_all", (1,)),
    "mqtt.cmd.send0": _mqtt_send(0),
    "mqtt.cmd.send1": _mqtt_send(1),
    "mqtt.cmd.auto0": _mqtt("sending AUTO to Edge 3", "write_mode",
                            (0, 'AUTO')),
    "mqtt.cmd.auto1": _mqtt("sending AUTO to Edge 4", "write_mode",
                            (1, 'AUTO')),
    "mqtt.cmd.manual0": _mqtt("sending MANUAL to Edge 3", "write_mode",
                              (0, 'MANUAL')),
    "mqtt.cmd.manual1": _mqtt("sending MANUAL to Edge 4", "write_mode",
                              (1, 'MANUAL')),
    "mqtt.out.cut": _mqtt("cutting Edge 3's link", "cut_edge3", (60,)),
    "mqtt.out.restore": _mqtt("restoring Edge 3's link", "restore_edge3"),
    "mqtt.out.rebirth": _mqtt("re-birthing Edge 3", "rebirth", (0,)),
    "mqtt.udt.diverge": _mqtt("changing Edge 4's layout", "udt_diverge",
                              (1,)),
    "mqtt.udt.putback": _mqtt("putting Edge 4's layout back", "udt_restore",
                              (1,)),
    "mqtt.udt.v2edge3": _mqtt("moving Edge 3 to v2", "udt_rollout_v2", (0,)),
    "mqtt.udt.v2edge4": _mqtt("moving Edge 4 to v2", "udt_rollout_v2", (1,)),
    "mqtt.udt.retire": _mqtt("retiring v1", "udt_retire_v1"),
    "mqtt.udt.reset": _mqtt("putting the templates back",
                            "udt_reset_baseline"),
    "mqtt.note.trip0": _mqtt("tripping Edge 3's fault", "trip_fault", (0,)),
    "mqtt.note.trip1": _mqtt("tripping Edge 4's fault", "trip_fault", (1,)),
}


def fire(session, action):
    """Run the rail action named `action`. Never raises.

    A RailButton's click handler is this call and nothing else, so the work a
    button does is decided in ACTIONS above rather than in a script inside a
    view -- which is what let eighteen copies of six bindings drift apart in
    the first place.
    """
    import demo_action

    key = str(action or "")
    try:
        build = ACTIONS.get(key)
        if build is None:
            return demo_action.note(session, key,
                                    "this button is not wired to anything")
        warning = CONFIRM.get(key)
        if warning and not demo_action.armed(session, key):
            return demo_action.arm(session, key, warning)
        what, go = build(session)
        return demo_action.run(session, key, what, go)
    except (Exception, JThrowable), e:
        _logger().warn("fire(%s) failed: %s" % (key, e))
        return demo_action.note(session, key, "could not start: %s"
                                % str(e)[:60])
