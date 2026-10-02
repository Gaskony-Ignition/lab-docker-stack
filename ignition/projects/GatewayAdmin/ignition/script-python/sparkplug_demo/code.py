"""The Sparkplug alarms demo: two isolated edges, one broker, one hub -- and the
customer question this exists to answer live: two edge nodes built from the
SAME UDT, modify one -- what happens on Engine to the type and to both
instances, and should they stay in sync?

THE THREE ROADS

  DATA        Edge3/Edge4 (Transmission) -> MQTT Distributor on the hub pair
              (ssl://mqtt-master:8883, fails over to mqtt-backup) -> MQTT
              Engine on the hub.
              Real Sparkplug B. This is what the tags/alarms/UDT panels show
              on the "cloud" side, and what a cloud-side write rides back on
              as a DCMD.
  OBSERVATION wd-control's MQTT listener (control/wire.py) copies the SAME
              broker traffic to a WebDev endpoint here (sparkplug/wire)
              purely so the console can show it decoded.
              It never feeds a tag and nothing here ever reads it as data --
              see docs/SPARKPLUG.md. Labelled as such on the page.
  the EDGE    each isolated edge also exposes a small read-only/actionable
              HTTP "observer" (its own WebDev, in its own `Edge` project) that
              this module polls directly for the edge's OWN view of its tags,
              alarms and UDT -- see the contract in DESIGN.md / SPARKPLUG.md.
              This is a SEPARATE thing from the OBSERVATION road above: this
              one calls INTO the edge; the wire witness is wd-control
              calling IN here. Both exist because neither can prove the other's claim --
              the observer proves what the edge thinks; the wire proves what
              actually crossed the broker; Engine proves what the cloud got.

EVERYTHING ENGINE-SIDE IS A GUESS, KEPT IN ONE PLACE

The tag paths under [MQTT Engine] were never measured against a running
Engine -- the live spike does that (see docs/SPARKPLUG.md). Every path is
built from _engine_node_folder()/_engine_tag_path() so the correction is one
edit, not a search-and-replace through this whole file.

EVERY OBSERVER CALL IS ISOLATED

One edge being down, slow or mid-restart must never blank the other edge's
numbers or the cloud side -- see _edge_state()'s cache and the fact that every
public function below either takes one edge at a time or loops calling
functions that already catch everything.

Jython 2.7: no f-strings, `except Exception, e:`, and `except Exception` does
NOT catch a java.lang.Throwable -- anything touching Java (system.secrets,
system.alarm, system.tag against a remote/mirrored provider) needs both.
"""
import struct

from java.lang import Throwable as JThrowable

# Reused, not reinvented: the store-and-forward demo's history-reapply/trend
# pattern (sf_demo._grid, in trend_series() below). The cut is wd-control's,
# through demo_control -- the same one Store & Forward's MQTT break uses.
import sf_demo

LOG = "SparkplugDemo"


def _logger():
    return system.util.getLogger(LOG)


def _now_millis():
    from java.lang import System as JSystem
    return JSystem.currentTimeMillis()


def _because(e):
    """An exception plus its cause chain -- system.net.httpClient wraps every
    transport failure as `IOException: Unable to <verb> <url>`, and the useful
    part is always the cause underneath it."""
    parts = [str(e)]
    try:
        cause = e.getCause()
        seen = 0
        while cause is not None and seen < 4:
            parts.append(str(cause))
            cause = cause.getCause()
            seen += 1
    except (Exception, JThrowable):
        pass
    return " <- ".join(parts)


# ------------------------------------------------------------------ constants
#
# Names fixed by scripts/sparkplug-*.sh on the EDGE side (see DESIGN.md
# "Names"). host/test_host come from stacks/ignition-edge3, ignition-edge4.
EDGES = [
    {"index": 0, "agent": "Edge3", "host": "ignition-edge3", "site": "North",
     "label": "Edge 3", "test_host": "edge3.test", "gwname": "Ignition-Edge3"},
    {"index": 1, "agent": "Edge4", "host": "ignition-edge4", "site": "South",
     "label": "Edge 4", "test_host": "edge4.test", "gwname": "Ignition-Edge4"},
]
GROUP = "AlarmDemo"

# The pump station's tags, in display order -- see DESIGN.md "Edge3 content".
# `name` is the short tag name under the site folder on BOTH sides (edge
# provider AND, assumed, the Engine mirror -- see ENGINE_* below).
TAGS = [
    {"name": "Level", "label": "Level", "unit": "%"},
    {"name": "Inflow", "label": "Inflow", "unit": ""},
    {"name": "PumpRunning", "label": "Pump Running", "unit": "", "boolean": True},
    {"name": "PumpFault", "label": "Pump Fault", "unit": "", "boolean": True},
    {"name": "DischargePressure", "label": "Discharge Pressure", "unit": ""},
    {"name": "LevelSetpoint", "label": "Level Setpoint", "unit": "%", "writable": True},
    {"name": "Mode", "label": "Mode", "unit": "", "writable": True, "enum": ["AUTO", "MANUAL"]},
    {"name": "Heartbeat", "label": "Heartbeat", "unit": ""},
]

# Alarms are on the UDT members, so a UDT change can also change alarm
# config -- part of what this demo exists to show (DESIGN.md).
ALARMS = [
    {"tag": "Level", "name": "Level High", "priority": "High"},
    {"tag": "Level", "name": "Level Low", "priority": "Medium"},
    {"tag": "PumpFault", "name": "Pump Fault", "priority": "Critical"},
    {"tag": "DischargePressure", "name": "Pressure High", "priority": "Medium"},
]

# The cloud's own link alarm is NOT in ALARMS: it has no edge half, no tag in
# the station, and it must not be counted as an active process alarm. Its
# priority is the one thing above them all -- an edge that has gone quiet means
# every row below is a guess. Written by scripts/sparkplug-setup.sh.
LINK_ALARM_PRIORITY = "Critical"

# A propagated alarm's source path is NOT built here any more --
# _alarm_view_rows() joins edge and cloud alarms on the alarm event id (the hub
# keeps the edge's id verbatim), which needs no path shape at all. Worth a note
# because the shape itself CHANGED: the exact-path matching this module used
# before the demo moved to Templates mode built
#   prot:MQTT:/src:<gwname>:/prov:MQTT Engine:/edgeProv:edge:/group_id:<group>:
#   /edge_node_id:<node>:/device_id:Pumps:/tag:<group>/Pumps/<site>/<tag>:/alm:<alarm>
# and Templates mode's real one (SPARKPLUG.md, 11/09/2026) is
#   .../edge_node_id:<node>:/device_id:Pumps:/metric_name:<site>/<member>:/alm:<name>
# -- "tag" became "metric_name". Nothing in this file builds either string any
# more, so this is history, not a trap to keep in sync.

# --- CLOUD-SIDE PATHS -- measured live 11/09/2026 by the EDGE agent, relayed
# by the lead. ONE constant block, on purpose: kept that way even now it is
# measured, because the notifications experiment and any future Engine
# setting change still only need one edit here.
#
# `convertUdts=True` (the default, tested first) flattens each edge's UDT
# instance into a plain DEVICE folder named after the Sparkplug DEVICE ID
# ("Pumps" -- both edges publish the same device name, one node each), with
# the station's tags one level further down under the SITE folder
# (North/South). Node-level metrics (Online, bdSeq, seq, birth/death/rebirth
# counts, Data Latency) live directly under the NODE folder, not under the
# device; Node Control/Rebirth and the device's own Device Control/Rebirth
# are separate writable tags -- an NCMD-triggered rebirth (cloud side) is
# therefore a DIFFERENT road from the observer's own "rebirth" POST (edge
# side), and the page offers both.
ENGINE_PROVIDER = "[MQTT Engine]"
ENGINE_NODES_ROOT = "Edge Nodes"
ENGINE_DEVICE = "Pumps"
ENGINE_TYPES_ROOT = "_types_"
ENGINE_UDT_NAME = "PumpStation"

# The hub's OWN folder in [default], written by sparkplug-setup.sh's
# configure_hub_cloud: one sub-folder per edge node holding the reference tags
# the hub alarms on itself -- PumpFault for the notification demo (C3), Online
# for the link alarm.
CLOUD_FOLDER = "SparkplugDemo"


def _engine_node_folder(edge):
    return "%s%s/%s/%s" % (ENGINE_PROVIDER, ENGINE_NODES_ROOT, GROUP, edge["agent"])


def _engine_device_folder(edge):
    return "%s/%s" % (_engine_node_folder(edge), ENGINE_DEVICE)


def _engine_site_folder(edge):
    return "%s/%s" % (_engine_device_folder(edge), edge["site"])


def _engine_notice_path(edge, name):
    """EdgeNotice/CloudNotice live directly under the DEVICE folder
    (.../Pumps/EdgeNotice), a sibling of the site folder -- NOT part of the
    PumpStation UDT instance, so NOT under _engine_site_folder(). Measured
    SPARKPLUG.md C2/C4."""
    return "%s/%s" % (_engine_device_folder(edge), name)


def _engine_tag_path(edge, tag_name):
    return "%s/%s" % (_engine_site_folder(edge), tag_name)


def _engine_node_info_path(edge, name):
    return "%s/Node Info/%s" % (_engine_node_folder(edge), name)


def _engine_node_control_rebirth_path(edge):
    return "%s/Node Control/Rebirth" % _engine_node_folder(edge)


def _engine_device_control_rebirth_path(edge):
    return "%s/Device Control/Rebirth" % _engine_device_folder(edge)


_NODE_ONLINE_CACHE = {}
NODE_ONLINE_TTL_MS = 1500


def _engine_node_online(edge_index):
    """Engine's OWN claim that this node is online (Node Info/Online) --
    the CLOUD-side fact, independent of whatever the edge's own observer
    says about itself. Returns None (not False) when it cannot be read, so
    a caller can tell "cloud says offline" from "cloud has no opinion".

    Deliberately a SEPARATE fact from the observer's transmission.connected,
    never blended into one badge -- they come from different ends of the
    wire and are not guaranteed to agree. NOT a claim that a disagreement is
    always benign: Edge3 was measured showing edge: NOT CONNECTED beside a
    wire log full of its own live DDATA (11/09/2026), which means the
    OBSERVER's own transmission.connected was simply wrong, not offering a
    second legitimate opinion -- being fixed on the edge side. Showing both
    facts is right regardless of which one turns out to be correct: it is
    what let this be FOUND, rather than hidden behind one blended badge that
    would have picked a side and been wrong.
    """
    now = _now_millis()
    cached = _NODE_ONLINE_CACHE.get(edge_index)
    if cached and (now - cached["at"]) < NODE_ONLINE_TTL_MS:
        return cached["value"]
    edge = _edge(edge_index)
    value = None
    if edge is not None:
        try:
            qv = system.tag.readBlocking([_engine_node_info_path(edge, "Online")])[0]
            if qv is not None and qv.value is not None:
                value = bool(qv.value)
        except (Exception, JThrowable):
            pass
    _NODE_ONLINE_CACHE[edge_index] = {"at": now, "value": value}
    return value


# The edge's own WebDev observer -- see the CONTRACT in DESIGN.md. Container
# name over `backbone`, exactly like gateway_admin.AGENTS' ping URLs.
def _observer_url(edge, path):
    return "http://%s:8088/system/webdev/Edge/sparkplug/%s" % (edge["host"], path)


OBSERVER_TIMEOUT_MS = 3000
STALE_MS = 15000


def _edge(index):
    try:
        return EDGES[int(index)]
    except (IndexError, ValueError, TypeError):
        return None


# --- reading the observer, isolated and cached ---------------------------------
#
# tag_rows(), _alarm_view_rows(), _route_lines() and page_udt() ALL want "what
# does this edge say about itself" on the same poll -- without a cache that is
# one HTTP round trip per binding, per 3s tick, per edge. The TTL is short enough that nothing on the page can ever
# disagree about which read it saw.
_STATE_CACHE = {}
STATE_TTL_MS = 1500


def _observer_get(edge, path):
    """One GET. Never raises -- an edge that is down, slow, or mid-restart is a
    STATE this page renders, not an exception that blanks the other edge."""
    url = _observer_url(edge, path)
    try:
        client = system.net.httpClient(timeout=OBSERVER_TIMEOUT_MS, version="HTTP_1_1")
        resp = client.get(url)
        if resp.good:
            return resp.json, None
        return None, "HTTP %d from %s" % (resp.statusCode, edge["label"])
    except (Exception, JThrowable), e:
        return None, "%s unreachable: %s" % (edge["label"], _because(e))


def _edge_state(edge_index):
    now = _now_millis()
    cached = _STATE_CACHE.get(edge_index)
    if cached and (now - cached["at"]) < STATE_TTL_MS:
        return cached["state"], cached["err"]
    edge = _edge(edge_index)
    if edge is None:
        return None, "no such edge"
    state, err = _observer_get(edge, "state")
    _STATE_CACHE[edge_index] = {"at": now, "state": state, "err": err}
    return state, err


def _wd_token():
    """The shared secret the observer's POST routes require, from the hub's
    own secret store -- installed by the EDGE agent's setup script, never
    bound to a view. Returns None (not "") when it is missing, so a caller can
    tell "no token yet" from "token is the empty string"."""
    try:
        with system.secrets.readSecretValue("wd", "sparkplug-token") as plain:
            return str(plain.getSecretAsString())
    except (Exception, JThrowable), e:
        _logger().warn("no sparkplug-token secret on this gateway yet: %s" % e)
        return None


def _observer_action(edge_index, action, body=None):
    """POST an action to one edge's observer. Returns {"ok", "message"} --
    never raises, because this is wired straight to a button."""
    edge = _edge(edge_index)
    if edge is None:
        return {"ok": False, "message": "no such edge"}
    token = _wd_token()
    if not token:
        return {"ok": False, "message": "no sparkplug-token secret installed on "
                                        "this gateway -- the edge setup script "
                                        "installs it"}
    payload = dict(body or {})
    payload["action"] = action
    url = _observer_url(edge, "action")
    headers = {"Content-Type": "application/json", "X-WD-Token": token}
    try:
        client = system.net.httpClient(timeout=OBSERVER_TIMEOUT_MS, version="HTTP_1_1")
        # A dict `data` with an explicit JSON content-type header.
        resp = client.post(url, headers=headers, data=payload)
        if resp.good:
            j = resp.json or {}
            return {"ok": bool(j.get("ok")), "message": str(j.get("message", ""))}
        return {"ok": False, "message": "HTTP %d from %s" % (resp.statusCode, edge["label"])}
    except (Exception, JThrowable), e:
        return {"ok": False, "message": "could not reach %s: %s" % (edge["label"], _because(e))}


def _action_message(result):
    if result.get("ok"):
        return result.get("message") or "Done."
    return result.get("message") or "Failed."


# --- edge actions (observer POSTs) ----------------------------------------------

def trip_fault(edge_index):
    return _action_message(_observer_action(edge_index, "trip_fault"))


def reset_fault(edge_index):
    return _action_message(_observer_action(edge_index, "reset_fault"))


def ack_edge(edge_index, alarm_id="all"):
    # The observer acknowledges EVERY unacknowledged alarm only when no id is
    # sent; an id of "all" is passed to system.alarm.acknowledge and fails (400).
    body = {} if alarm_id in (None, "", "all") else {"id": alarm_id}
    result = _observer_action(edge_index, "ack_local", body)
    # The observer answers 400 when it had nothing unacknowledged -- typically
    # right after an Ack in the cloud already reached the edge. Say that plainly.
    if not result.get("ok") and str(result.get("message", "")).startswith("HTTP 400"):
        return "Nothing left to acknowledge at %s (the edge answered HTTP 400)." % _short_name(_edge(edge_index))
    return _action_message(result)


def rebirth(edge_index):
    """EDGE-side rebirth: the observer's own POST, i.e. the edge decides to
    resend its birth certificates. See rebirth_cloud() for the other road --
    writing Engine's Node Control/Rebirth, which rides an NCMD DOWN to the
    edge and asks IT to rebirth. DESIGN.md's UDT prediction explicitly asks
    whether an edit rebirths on its own or needs one of these; the page
    offers both so that question can be answered by clicking, not guessing.

    Scenario 4's Rebirth button: `Refresh Edge Node`, a death and a birth.
    Measured 22/09/2026 (SPARKPLUG.md, Three field questions, C): 2.4-3.0 s
    with nothing from the node, and the rolling buffer refills it -- 0 of 50
    heartbeats lost."""
    result = _action_message(_observer_action(edge_index, "rebirth"))
    if edge_index == CUT_EDGE_INDEX:
        _record_cut("Rebirth pressed")
    return result


def rebirth_cloud(edge_index):
    """CLOUD-side rebirth: write Engine's Node Control/Rebirth tag (measured
    11/09/2026), which Engine turns into an NCMD back to the edge node --
    no HTTP, no observer, same mechanism as write_setpoint()/write_mode()."""
    edge = _edge(edge_index)
    if edge is None:
        return "No such edge."
    path = _engine_node_control_rebirth_path(edge)
    try:
        results = system.tag.writeBlocking([path], [True])
        quality = str(results[0]) if results else "?"
    except (Exception, JThrowable), e:
        return "Write to %s failed: %s" % (path, _because(e))
    return ("Wrote Node Control/Rebirth on %s (quality %s) -- an NCMD went "
            "down; watch for a fresh NBIRTH on the Wire tab." % (edge["label"], quality))


def udt_diverge(edge_index, variant="add_member"):
    """MEASURED LIVE (11/09/2026, SPARKPLUG.md T8): the demo runs Sparkplug
    TEMPLATES (convertUdts=false + publishUdtDefinitions=true), and under
    Templates the default node-scope rebirth ("Refresh Edge Node") RE-SENDS
    A CACHED DEFINITION rather than re-reading the edge's changed one -- four
    variants in a row published the SAME md5 as before the edit. Only a
    MODULE-WIDE refresh (`[MQTT Transmission]Transmission Control/Refresh`,
    or re-saving the transmitter) makes Transmission re-read the UDT, which
    is what actually produces Engine's "UDT definition collision detected"
    warning. `rebirth: true, scope: module` on the POST is what asks the
    edge's own action handler to use that lever instead of the session
    bounce -- without it, clicking Diverge would look like nothing happened
    on the cloud side, for a completely different reason than the customer
    question this button exists to demonstrate."""
    return _action_message(_observer_action(edge_index, "udt_diverge",
                            {"variant": variant, "rebirth": True, "scope": "module"}))


def udt_restore(edge_index):
    return _action_message(_observer_action(edge_index, "udt_restore",
                            {"rebirth": True, "scope": "module"}))


# --- cutting the broker link --------------------------------------------------
#
# wd-control takes Edge3's container off `wd-mqtt`, the network that carries
# MQTT only, and puts it back when the time runs out (control/mqttcut.py,
# through demo_control.mqtt_cut -- the same cut Store & Forward's MQTT break
# uses). Backbone stays, so the observer keeps answering through the cut.
# MEASURED (docs/MQTT-DISTRIBUTOR.md T-D12): nothing is sent for the first
# ~15 s -- the broker only gives up at 1.5 x keepalive (10 s) and then
# publishes the edge's Last Will, and Engine marks the node offline. Until
# then the only sign is the missing messages (WIRE_STALE_MS). Fixed to Edge3:
# one demonstrable cut is the point, not a generic break-any-edge control.

CUT_EDGE_INDEX = 0  # Edge3 (North)
CUT_STACK = "ignition-edge3"


def cut_edge3(seconds=60):
    import demo_control
    ok, why = demo_control.mqtt_cut(CUT_STACK, seconds)
    if not ok:
        return "Could not cut Edge 3: %s" % why
    _set_cut_until(_now_millis() + int(seconds) * 1000)
    _record_cut("Link cut for %d s with the Cut button" % int(seconds))
    _record_cut_start(seconds)
    return ("Cut Edge 3's broker link for %d seconds. Watch the messages stop at "
            "once, and the cloud mark it offline about 15 s later, when the broker "
            "gives up on it." % seconds)


def restore_edge3():
    import demo_control
    was_cut = _cut_remaining_s() > 0
    ok, why = demo_control.mqtt_restore(CUT_STACK)
    if not ok:
        return "Could not restore Edge 3: %s" % why
    _record_cut("Restored by hand" if was_cut else "Restore pressed; nothing was cut")
    _record_cut_end()
    _set_cut_until(0)
    return ("Restored Edge 3's broker link -- it reconnects within a few "
            "seconds; buffered values and alarm events are on their way up.")


# --- cloud writes: the Engine mirror -> DCMD -> the edge follows ---------------
#
# Writing MQTT Engine's own mirrored tag is the whole mechanism -- Engine
# turns a write to one of its Sparkplug-sourced tags into a DCMD/NCMD back
# down to the originating edge node. No HTTP, no observer, nothing edge-side
# to call; that is what makes this the "cloud -> edge" road rather than a
# second copy of the observer's set_mode.

def write_setpoint(edge_index, value):
    edge = _edge(edge_index)
    if edge is None:
        return "No such edge."
    path = _engine_tag_path(edge, "LevelSetpoint")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "Not a number: %r" % (value,)
    try:
        results = system.tag.writeBlocking([path], [value])
        quality = str(results[0]) if results else "?"
    except (Exception, JThrowable), e:
        return "Write to %s failed: %s" % (path, _because(e))
    return ("Wrote Level Setpoint = %s on %s (quality %s) -- watch the edge "
            "value follow via DCMD." % (value, edge["label"], quality))


def write_mode(edge_index, mode):
    edge = _edge(edge_index)
    if edge is None:
        return "No such edge."
    path = _engine_tag_path(edge, "Mode")
    try:
        results = system.tag.writeBlocking([path], [str(mode)])
        quality = str(results[0]) if results else "?"
    except (Exception, JThrowable), e:
        return "Write to %s failed: %s" % (path, _because(e))
    return ("Wrote Mode = %s on %s (quality %s) -- watch the edge value "
            "follow via DCMD." % (mode, edge["label"], quality))


# --- cloud alarms: real Ignition alarms in the hub's own status table ---------

def _alarm_get(row, name):
    """Guard EACH accessor separately -- AlarmEvent.isActive() throws, and
    wrapping a whole row in one try/except turns standing alarms into an
    empty list, which reads as healthy (verified in this toolkit's own
    knowledge base). Never call .isActive(); derive from getState()'s string."""
    try:
        return getattr(row, name)()
    except (Exception, JThrowable):
        return None


def _alarm_row_from(row):
    state = _alarm_get(row, "getState")
    state_str = str(state) if state is not None else ""
    ack_user = _alarm_get(row, "getAckUser")
    return {
        "id": str(_alarm_get(row, "getId") or ""),
        "name": str(_alarm_get(row, "getName") or ""),
        "priority": str(_alarm_get(row, "getPriority") or ""),
        "state": state_str or "?",
        "active": "Active" in state_str,
        "acked": "Unack" not in state_str,
        "ackedBy": str(ack_user) if ack_user else "",
        "displayPath": str(_alarm_get(row, "getDisplayPath") or ""),
        "source": str(_alarm_get(row, "getSource") or ""),
    }


_ALARM_CACHE = {"at": 0, "rows": []}
ALARM_TTL_MS = 1500


def _all_cloud_alarms():
    now = _now_millis()
    if _ALARM_CACHE["rows"] and (now - _ALARM_CACHE["at"]) < ALARM_TTL_MS:
        return _ALARM_CACHE["rows"]
    try:
        raw = system.alarm.queryStatus()
    except (Exception, JThrowable), e:
        _logger().warn("system.alarm.queryStatus failed: %s" % e)
        return []
    rows = [_alarm_row_from(r) for r in (raw or [])]
    _ALARM_CACHE["at"] = now
    _ALARM_CACHE["rows"] = rows
    return rows


# The hub's OWN alarm -- a reference tag on Engine's PumpFault, alarmed
# directly in the hub's [default] provider and bound to the hub's own
# CloudNotify pipeline (SPARKPLUG.md C3/C5). NOT a propagated Sparkplug
# alarm: it is what makes CloudNotify possible at all, since a
# Sparkplug-propagated alarm event never runs a hub pipeline. Excluded from
# the Alarms tab (which compares an edge alarm against ITS OWN propagation)
# and surfaced instead on the Notifications tab, where it belongs.
CLOUD_OWN_ALARM_NAME = "Pump Fault (cloud)"


def _cloud_alarms_by_id():
    """MEASURED LIVE (SPARKPLUG.md T2): the hub keeps the edge's own alarm
    event id verbatim (16 of 16 of edge3's ids matched, byte for byte), so
    joining on `id` is simpler and exact, and does not have to know the
    source-path shape at all (which changed under Templates mode anyway -- the
    note above ALARMS records the old shape). Used by _alarm_view_rows(). An
    alarm that has NEVER gone active on the cloud side is
    correctly ABSENT from system.alarm.queryStatus() entirely -- Ignition
    does not list an alarm it has never instantiated -- so a missing id
    here is an honest "this has not happened yet", not a matching bug.
    CLOUD_OWN_ALARM_NAME is excluded explicitly (belt and braces -- its id
    would never match an edge alarm's id anyway, since it is a different
    alarm entirely)."""
    out = {}
    for r in _all_cloud_alarms():
        if not r.get("id") or r.get("name") == CLOUD_OWN_ALARM_NAME:
            continue
        out[r["id"]] = r
    return out


ACK_USER = "sparkplug-demo-console"


# --- tag comparison -------------------------------------------------------------

def _fmt_ts(ms):
    if not ms:
        return DASH
    try:
        return system.date.format(system.date.fromMillis(long(ms)), "HH:mm:ss.SSS")
    except (Exception, JThrowable):
        return DASH


def _fmt_val(value, spec):
    if value is None:
        return DASH
    if spec.get("boolean"):
        return "TRUE" if value else "false"
    if isinstance(value, (int, long, float)) and not isinstance(value, bool):
        return ("%.1f" % value) + (spec.get("unit") or "")
    return str(value) + (spec.get("unit") or "")


def _mismatch(edge_val, cloud_val, spec):
    if edge_val is None or cloud_val is None:
        return False
    if spec.get("boolean"):
        return bool(edge_val) != bool(cloud_val)
    try:
        return abs(float(edge_val) - float(cloud_val)) > 0.5
    except (TypeError, ValueError):
        return str(edge_val) != str(cloud_val)


_LATENCY_CACHE = {}
LATENCY_TTL_MS = 2000


def _engine_data_latency_ms(edge_index):
    """Engine's OWN measured node latency (Node Info/Data Latency), read
    once per short window and reused across every row of that edge.

    NOT `cloud_ts - edge_ts` per tag: measured live 11/09/2026 that Engine
    stamps a Sparkplug-sourced tag with the METRIC'S OWN timestamp (the one
    the edge put in the payload), so that subtraction is ~0 by construction
    and would show a latency figure that is not actually measuring anything
    -- it would read as "perfect" even if Engine had not heard from the edge
    in an hour. Data Latency is Engine's real, continuously updated answer
    to "how far behind is this node", so it is what the page shows instead.
    """
    now = _now_millis()
    cached = _LATENCY_CACHE.get(edge_index)
    if cached and (now - cached["at"]) < LATENCY_TTL_MS:
        return cached["value"]
    edge = _edge(edge_index)
    value = None
    if edge is not None:
        try:
            qv = system.tag.readBlocking([_engine_node_info_path(edge, "Data Latency")])[0]
            if qv is not None and qv.value is not None:
                value = qv.value
        except (Exception, JThrowable):
            pass
    _LATENCY_CACHE[edge_index] = {"at": now, "value": value}
    return value


def tag_rows(edge_index):
    edge = _edge(edge_index)
    if edge is None:
        return []
    state, err = _edge_state(edge_index)
    edge_tags = {}
    if state:
        for t in (state.get("tags") or []):
            name = str(t.get("path", "")).split("/")[-1]
            edge_tags[name] = t

    latency_ms = _engine_data_latency_ms(edge_index)
    latency_text = ("Engine: %s ms" % latency_ms) if latency_ms is not None else "--"
    # MEASURED LIVE (SPARKPLUG.md T1): edge-to-cloud is ~1.04 s end to end,
    # and that whole second is Transmission's OWN tagPacingPeriod (how often
    # the edge paces a DDATA out) -- the broker leg underneath it is 2-4 ms.
    # Next to the number so "why is this a second and not milliseconds" is
    # answered on the page, not left looking like a slow network.
    latency_note = "~1s here is Transmission's tagPacingPeriod, not the network (broker leg: 2-4 ms)"

    now = _now_millis()
    rows = []
    for spec in TAGS:
        name = spec["name"]
        et = edge_tags.get(name, {})
        e_val = et.get("value")
        e_ts = et.get("ts")
        cpath = _engine_tag_path(edge, name)
        c_val, c_qual, c_ts = None, "", None
        try:
            qv = system.tag.readBlocking([cpath])[0]
            if qv is not None and qv.value is not None:
                c_val = qv.value
                c_ts = qv.timestamp.getTime() if qv.timestamp else None
            if qv is not None and qv.quality is not None:
                c_qual = str(qv.quality)
        except (Exception, JThrowable):
            pass

        # cloudTs is the metric's OWN (edge-stamped) timestamp, not when
        # Engine received it -- see _engine_data_latency_ms()'s docstring.
        # "(edge ts)" not "(edge-stamped)" -- the longer form sliced mid-word
        # to "(edge-star" in the Tags column at 1440x900 (1440x900 review,
        # 11/09/2026); shortening it is honest, not a truncation.
        cloud_ts_text = (_fmt_ts(c_ts) + " (edge ts)") if c_ts else "--"
        rows.append({
            "name": spec["label"],
            "edgeValue": _fmt_val(e_val, spec), "edgeQuality": str(et.get("quality", "--")),
            "edgeTs": _fmt_ts(e_ts),
            "cloudValue": _fmt_val(c_val, spec), "cloudQuality": c_qual or "--",
            "cloudTs": cloud_ts_text,
            "latency": latency_text,
            "latencyNote": latency_note,
            "writable": bool(spec.get("writable")),
            "isMode": bool(spec.get("enum")),
            "stale": bool(e_ts) and (now - e_ts) > STALE_MS,
            "mismatch": _mismatch(e_val, c_val, spec),
            "edgeUnreachable": state is None,
        })
    return rows


# --- alarm comparison -------------------------------------------------------------


# --- notifications (SPARKPLUG.md "Notifications over MQTT", Phase C) ----------
#
# FOUR roads, not one, and only two of them actually deliver:
#   C1/C5  the edge's OWN pipeline (Alarm Notification module, runs at the
#          edge) -- observer `notifications`, newest first, <=20.
#   C2a    edge -> cloud over SPARKPLUG: EdgeNotice, an ordinary String
#          metric beside the station -- WORKS, 0.5-1.0s.
#   C2b    edge -> cloud over RAW MQTT: publish() to notify/AlarmDemo/<node>
#          -- publish() reports "ok" even when it delivers nothing (it did,
#          while the RPC client lacked the broker's CA), so an empty list
#          has to be shown as genuinely EMPTY, not a read failure.
#   C4     cloud -> edge: CloudNotice, an Engine tag write that rides a
#          DCMD -- WORKS, ~4ms broker leg.
# C3 is the one that DOESN'T exist: a Sparkplug-propagated alarm never runs
# a hub pipeline (Engine keeps the edge's pipeline properties but never
# evaluates them) -- the hub's own CloudNotify only runs off CLOUD_OWN_ALARM,
# a reference tag on Engine's PumpFault, alarmed directly in [default].

CLOUD_NOTIFY_LOG_PATH = "[default]SparkplugDemo/CloudNotifyLog"


def _engine_notice(edge_index, name):
    """One EdgeNotice/CloudNotice read from Engine, with its own timestamp
    (the metric's, i.e. when the EDGE wrote it, not when Engine polled)."""
    edge = _edge(edge_index)
    if edge is None:
        return {"raw": "", "ts": None}
    try:
        qv = system.tag.readBlocking([_engine_notice_path(edge, name)])[0]
    except (Exception, JThrowable):
        return {"raw": "", "ts": None}
    if qv is None or qv.value is None:
        return {"raw": "", "ts": None}
    ts = qv.timestamp.getTime() if qv.timestamp else None
    return {"raw": str(qv.value), "ts": ts}


def cloud_notify_log():
    """C3/C5 -- the hub pipeline's OWN record, [default]SparkplugDemo/
    CloudNotifyLog, a JSON list of at most 20 entries. Written only when
    CLOUD_OWN_ALARM fires -- a propagated Sparkplug alarm never reaches
    this, which is the whole point of C3."""
    try:
        qv = system.tag.readBlocking([CLOUD_NOTIFY_LOG_PATH])[0]
    except (Exception, JThrowable):
        return None
    if qv is None or qv.value is None:
        return None
    try:
        data = system.util.jsonDecode(str(qv.value))
    except (Exception, JThrowable):
        return None
    return data if isinstance(data, list) else None


# FIXED 11/09/2026 (EDGE agent, sparkplug-edge d85352f): the RPC client now
# has its own CA + credentials, so the raw-MQTT road delivers for real. Each
# JSON key lands as its OWN tag under .../Notify/notify/AlarmDemo/<node>/
# <key> -- flat, not one document -- so a key ABSENT from a newer message
# simply keeps its stale value from whichever earlier message last wrote it
# (a burst-test message's `test`/`i`/`of` can sit right beside a later real
# alarm message's fields). There is no per-key "which message" marker, so
# this groups by RECEIVE TIME instead: every key genuinely written by the
# same incoming payload lands on the hub within a few ms of each other, so a
# tag is "current" if its own receive timestamp is close to the `ts` tag's
# (the document's own embedded event time, re-published every message) --
# anything further out is a leftover from whatever message wrote it last.
RAW_NOTIFY_GROUP_TOLERANCE_MS = 500


def _raw_notify_tags(edge):
    root = "%sNotify/notify/AlarmDemo/%s" % (ENGINE_PROVIDER, edge["agent"])
    try:
        results = system.tag.browse(root).getResults()
    except (Exception, JThrowable):
        return []
    out = []
    for r in results or []:
        try:
            path = "%s/%s" % (root, r["name"])
            qv = system.tag.readBlocking([path])[0]
            if qv is None or qv.value is None:
                continue
            recv_ts = qv.timestamp.getTime() if qv.timestamp else None
            out.append({"name": str(r["name"]), "value": qv.value, "recvTs": recv_ts})
        except (Exception, JThrowable):
            continue
    return out


def raw_notify_grouped(edge_index):
    """Returns {"empty", "current" (this message's keys), "stale" (leftover
    from an earlier message), "eventTs" (the document's own `ts`),
    "latencyMs" (hub receive time - eventTs, i.e. the raw road's real
    edge-to-cloud latency, NOT the tag's own receive timestamp which is
    only ever the HUB's clock)."""
    edge = _edge(edge_index)
    if edge is None:
        return {"empty": True, "current": [], "stale": [], "eventTs": None, "latencyMs": None}
    tags = _raw_notify_tags(edge)
    if not tags:
        return {"empty": True, "current": [], "stale": [], "eventTs": None, "latencyMs": None}
    by_name = dict((t["name"], t) for t in tags)
    ts_tag = by_name.get("ts")
    if ts_tag is not None and ts_tag["recvTs"] is not None:
        anchor = ts_tag["recvTs"]
    else:
        anchor = max((t["recvTs"] or 0) for t in tags)
    current, stale = [], []
    for t in sorted(tags, key=lambda x: x["name"]):
        recv = t["recvTs"]
        row = {"name": t["name"], "value": str(t["value"])}
        if recv is not None and abs(recv - anchor) <= RAW_NOTIFY_GROUP_TOLERANCE_MS:
            current.append(row)
        else:
            stale.append(row)
    event_ts, latency_ms = None, None
    if ts_tag is not None:
        try:
            event_ts = int(ts_tag["value"])
        except (Exception, JThrowable):
            event_ts = None
        if event_ts is not None and ts_tag["recvTs"] is not None:
            latency_ms = ts_tag["recvTs"] - event_ts
    return {"empty": False, "current": current, "stale": stale,
            "eventTs": event_ts, "latencyMs": latency_ms}


# --- UDT panel -------------------------------------------------------------------


def _engine_instance_members(edge):
    try:
        return [str(r["name"]) for r in
                system.tag.browse(_engine_site_folder(edge)).getResults()]
    except (Exception, JThrowable):
        return []


# --- cards (params for the shared GatewayCard view) -----------------------------

# MEASURED (MQTT-DISTRIBUTOR.md T-D12): when the network to the broker drops,
# nothing tells anybody at once. The broker waits 1.5 x keepalive (10 s) before
# publishing the edge's Last Will, so Engine's Node Info/Online stays ONLINE,
# holding the last values as Good, for the first ~15 s of a cut. (On EMQX a
# broker-side kick sent no Last Will at all, and Online stayed true for the
# whole 62 s: SPARKPLUG.md T6.) The wire witness's own last-seen timestamp is
# the only thing here that sees the outage from its first second. Five seconds is comfortably above the ~1s DDATA
# pacing (see tag_rows()'s latencyNote) and the delivery jitter that shows up
# in the wire log, and comfortably below the 60 s cut the page itself offers.
WIRE_STALE_MS = 5000


def _wire_last_seen_ms(edge_index):
    """When the wire witness last saw a DDATA/NDATA from this edge's node --
    None if it has never seen one."""
    edge = _edge(edge_index)
    if edge is None:
        return None
    store = _wire_store()
    node = edge["agent"]
    last_ts = 0
    for m in store["messages"]:
        if m.get("type") not in ("DDATA", "NDATA"):
            continue
        if m.get("node") != node:
            continue
        if m["ts"] > last_ts:
            last_ts = m["ts"]
    return last_ts or None


def _wire_last_seen_age_ms(edge_index):
    """Milliseconds since the wire witness last saw a DDATA/NDATA from this
    edge's node -- None if it has never seen one. Independent of Engine's
    own Node Info/Online, which lags a cut by ~15 s (see WIRE_STALE_MS
    above)."""
    last_ts = _wire_last_seen_ms(edge_index)
    if last_ts is None:
        return None
    return _now_millis() - last_ts


def _last_heard_ms(edge_index):
    """When the cloud last heard from this edge, for the "as at" on a stale
    row. The wire witness first: it sees the outage from its first second. Its
    ring is emptied by a script-library restart, and Engine's own copy of
    Heartbeat keeps the stamp of the last value that arrived, so that is the
    fallback."""
    ms = _wire_last_seen_ms(edge_index)
    if ms:
        return ms
    edge = _edge(edge_index)
    if edge is None:
        return None
    try:
        qv = system.tag.readBlocking([_engine_tag_path(edge, "Heartbeat")])[0]
        if qv is not None and qv.timestamp is not None:
            return qv.timestamp.getTime()
    except (Exception, JThrowable):
        pass
    return None


def _edge_live(edge_index):
    """Whether this edge is ACTUALLY feeding the hub right now -- Engine
    saying Online AND the wire witness having heard from it within
    WIRE_STALE_MS. For the first ~15 s of a cut the first half of that
    stays true (see WIRE_STALE_MS's docstring), so it is the second half
    that catches the outage first. `age is None` (never
    witnessed anything for this node, e.g. moments after a restart) is
    treated as "trust Engine" rather than shown as stale."""
    online = _engine_node_online(edge_index)
    if not online:
        return False
    age = _wire_last_seen_age_ms(edge_index)
    return age is None or age <= WIRE_STALE_MS


# --- the wire -- decoded Sparkplug messages, kept by record_wire() below -------
#
# record_wire() is called from the WebDev POST handler
# (com.inductiveautomation.webdev/resources/sparkplug/wire/doPost.py), which
# wd-control's MQTT listener calls for every message on spBv1.0/AlarmDemo/#
# (+ STATE/# + the reserved notify/# topic) -- see control/wire.py. Kept in system.util.getGlobals() rather than a tag or a database: it
# is genuinely disposable, in-memory, ring-buffered state, and the one honest
# thing to say about it is that a script library restart empties it -- same
# trade this repo already made for the redundancy demo's stop_peer() state.
WIRE_GLOBAL_KEY = "sparkplug_wire"
WIRE_RING_SIZE = 250
WIRE_TYPE_ORDER = ["NBIRTH", "DBIRTH", "NDATA", "DDATA", "NCMD", "DCMD", "STATE", "NOTIFY"]

# THE STORY IS NOT DDATA. Measured 11/09/2026: at ~2 DDATA/s the 250-row ring
# above turns over in about two minutes, so NBIRTH/DBIRTH/NCMD/DCMD/STATE --
# the actual customer-facing events -- scroll off screen within seconds of
# happening and the panel reads as "just a tag feed". EVENTS is a SECOND,
# separate ring that only ever receives the message types below (plus any
# DDATA/NDATA carrying a metric whose name contains "alarm" -- Sparkplug rides
# alarms as ordinary metrics, see DESIGN.md), so a birth, a death, a command
# or an alarm survives long after the DDATA that arrived in the same second
# has been pushed out of the other ring.
WIRE_EVENT_TYPES = set(["NBIRTH", "DBIRTH", "NDEATH", "DDEATH", "NCMD", "DCMD", "STATE", "NOTIFY"])
WIRE_EVENTS_RING_SIZE = 150
WIRE_COMMANDS_RING_SIZE = 100


def _wire_store():
    g = system.util.getGlobals()
    if WIRE_GLOBAL_KEY not in g:
        g[WIRE_GLOBAL_KEY] = {"messages": [], "events": [], "counts": {}, "started": _now_millis()}
    store = g[WIRE_GLOBAL_KEY]
    store.setdefault("events", [])   # upgrade a store created before this ring existed
    # Commands only: the events ring turns over on plain-MQTT notifications within
    # minutes, which left the page's "commands the cloud sent" list empty.
    store.setdefault("commands", [])
    return store


def _topic_kind(topic):
    """(type, group, node, device, direction) from a Sparkplug/notify topic.
    Sparkplug encodes all of this in the TOPIC, not the payload, so this never
    touches the protobuf."""
    parts = str(topic).split("/")
    if parts and parts[0] == "spBv1.0":
        if len(parts) > 1 and parts[1] == "STATE":
            return "STATE", "", parts[2] if len(parts) > 2 else "", "", "broker"
        if len(parts) >= 4:
            group, mtype, node = parts[1], parts[2], parts[3]
            device = parts[4] if len(parts) > 4 else ""
            direction = "cloud -> edge" if mtype in ("NCMD", "DCMD") else "edge -> cloud"
            return mtype, group, node, device, direction
    if parts and parts[0] == "notify":
        # notify/AlarmDemo/<node> -- parts[1] is the GROUP, parts[2] the
        # node; before this fix the node column showed "AlarmDemo" for
        # every row, invisible while this road delivered nothing and only
        # noticed once it started (fixed 11/09/2026, EDGE agent).
        return ("NOTIFY", parts[1] if len(parts) > 1 else "",
                parts[2] if len(parts) > 2 else "", "", "edge -> cloud")
    return "UNKNOWN", "", "", "", "-"


def record_wire(topic, raw_bytes):
    """Decode one message the witness handed us and append it to the ring
    buffer. Called from the WebDev doPost handler -- never raises past here,
    because a bad message must not take the ingest route down for the rest."""
    now = _now_millis()
    mtype, group, node, device, direction = _topic_kind(topic)

    metrics, seq, decode_error = [], None, ""
    if mtype == "STATE":
        try:
            metrics = [{"name": "state", "value": str(raw_bytes)[:64]}]
        except (Exception, JThrowable):
            pass
    elif mtype == "NOTIFY":
        # The raw-MQTT notification road (SPARKPLUG.md C2b) -- plain JSON
        # text, not a Sparkplug protobuf payload. Fixed 11/09/2026 (EDGE
        # agent): before that this road delivered nothing, so every NOTIFY
        # falling through to _decode_payload() below never actually ran.
        # Now that it does, it decoded every one as "unsupported wire type
        # 3" -- a JSON document is not a protobuf message.
        try:
            doc = system.util.jsonDecode(str(raw_bytes))
            if isinstance(doc, dict):
                for k, v in doc.items():
                    metrics.append({"name": str(k), "value": v})
            else:
                metrics = [{"name": "notify", "value": str(raw_bytes)[:120]}]
        except (Exception, JThrowable), e:
            decode_error = str(e)
    else:
        try:
            decoded = _decode_payload(raw_bytes)
            seq = decoded.get("seq")
            for m in decoded.get("metrics", []):
                name = m.get("name")
                if name is None:
                    name = "(alias %s)" % m.get("alias", "?")
                name = str(name)
                value = m.get("value")
                datatype = m.get("datatype")
                if datatype == "Template" and isinstance(value, list):
                    # A UDT instance's Template value nests the ACTUAL
                    # station members (Level, Inflow, ...) -- flatten to
                    # "<instance>/<member>" so the wire panel shows a real
                    # tag name and a real value, not the instance name
                    # three times over with value=None (SPARKPLUG.md
                    # Templates mode; see _template()'s docstring).
                    if value:
                        for nested in value:
                            nested_name = nested.get("name")
                            if nested_name is None:
                                continue
                            metrics.append({"name": "%s/%s" % (name, nested_name),
                                             "value": nested.get("value"),
                                             "datatype": nested.get("datatype")})
                    else:
                        metrics.append({"name": name, "value": "(empty template)", "datatype": datatype})
                elif datatype == "DataSet" and isinstance(value, dict):
                    rows = value.get("rows", 0)
                    metrics.append({"name": name,
                                     "value": "0 rows" if not rows else "%d row%s" % (rows, "" if rows == 1 else "s"),
                                     "datatype": datatype})
                elif value is None:
                    # MEASURED (?debug=lastddata): AlarmReconciliation
                    # (DataSet-typed) genuinely carries no value field at
                    # all when there is nothing to reconcile -- Sparkplug's
                    # is_null, not a decode gap (the DataSet/Template
                    # branches above already catch a PRESENT-but-decoded
                    # value; this is "the field was never there"). Spelled
                    # out so it cannot read as a value this decoder failed
                    # to find, the exact ambiguity this whole fix chases.
                    metrics.append({"name": name, "value": "(null)", "datatype": datatype})
                else:
                    metrics.append({"name": name, "value": value, "datatype": datatype})
        except (Exception, JThrowable), e:
            decode_error = str(e)

    row = {"ts": now, "topic": str(topic), "type": mtype, "group": group,
           "node": node, "device": device, "direction": direction,
           "seq": seq, "metrics": metrics, "error": decode_error}

    store = _wire_store()
    store["counts"][mtype] = store["counts"].get(mtype, 0) + 1
    store["messages"].append(row)
    if len(store["messages"]) > WIRE_RING_SIZE:
        del store["messages"][:-WIRE_RING_SIZE]

    is_alarm_metric = any("alarm" in str(m.get("name", "")).lower() for m in metrics)
    if mtype in WIRE_EVENT_TYPES or is_alarm_metric:
        if mtype in ("NCMD", "DCMD"):
            commands = store["commands"]
            commands.append(row)
            if len(commands) > WIRE_COMMANDS_RING_SIZE:
                del commands[:-WIRE_COMMANDS_RING_SIZE]
        events = store["events"]
        events.append(row)
        if len(events) > WIRE_EVENTS_RING_SIZE:
            del events[:-WIRE_EVENTS_RING_SIZE]


def wire_summary():
    store = _wire_store()
    now = _now_millis()
    recent = [m for m in store["messages"] if now - m["ts"] <= 10000]
    counts = store.get("counts", {})
    count_rows = [{"type": t, "count": counts.get(t, 0)} for t in WIRE_TYPE_ORDER if counts.get(t)]
    return {
        "total": len(store["messages"]),
        "rate": round(len(recent) / 10.0, 1),
        "counts": count_rows,
        "flowing": len(recent) > 0,
        "startedAgo": int((now - store.get("started", now)) / 1000),
    }


# A metric's value can be a plain scalar ("53.8") or, when a DDATA happens
# to carry an alarm-state dataset (AlarmReconciliation and similar), a
# stringified Java object several KB long -- measured live 11/09/2026 via
# ?debug=nodelines: one Edge4/Pumps message's "last" line alone ran past
# 4,000 characters of serialized AlarmEvent JSON. Left untruncated, that
# single bit dominates (and can visually break) the one-line-per-node
# summary this function promises to be. 24 chars is enough to recognise a
# real value ("53.8", "TRUE", "AUTO") without one metric drowning the rest.
# Shared by wire_node_lines() and wire_event_rows() -- both read the SAME
# record_wire()-decoded metrics list, so a value that can blow one line up can
# blow up the other.
WIRE_NODE_VALUE_MAX_LEN = 24


def _wire_metric_bit(mm):
    value_text = str(mm.get("value"))
    if len(value_text) > WIRE_NODE_VALUE_MAX_LEN:
        value_text = value_text[:WIRE_NODE_VALUE_MAX_LEN] + "..."
    return "%s=%s" % (mm.get("name"), value_text)


def wire_node_lines():
    """One compact, non-scrolling line per node/device seen in the last
    window -- the DDATA rate itself, and its last few values. This is what
    the page shows for "is data flowing right now"; wire_event_rows() below
    is where the actual story (births, deaths, commands, STATE, alarms)
    survives the flood this would otherwise bury it in.

    A node can genuinely have a rate > 0 and an age of "0s ago" (a message
    really did just arrive) while still carrying NO metrics to show -- a
    Sparkplug DDATA/NDATA is not required to carry any (a bare sequence
    advance, or a decode that found nothing to extract). MEASURED (1440x900
    ship check, 11/09/2026): that case previously rendered as "last: --",
    which a pixel-level check misread as empty background -- two hyphens at
    9-10px monospace are easy to miss entirely at a glance, which is the
    same failure the blank case itself was flagged for. "none yet" cannot
    be mistaken for nothing."""
    store = _wire_store()
    now = _now_millis()
    by_node = {}
    for m in store["messages"]:
        if m.get("type") not in ("DDATA", "NDATA"):
            continue
        key = "%s/%s" % (m["node"], m["device"]) if m.get("device") else m.get("node", "?")
        rec = by_node.setdefault(key, {"count10": 0, "last": None, "lastTs": 0})
        if now - m["ts"] <= 10000:
            rec["count10"] += 1
        if m["ts"] >= rec["lastTs"]:
            rec["lastTs"] = m["ts"]
            rec["last"] = m
    out = []
    for key in sorted(by_node):
        rec = by_node[key]
        m = rec["last"] or {}
        bits = [_wire_metric_bit(mm) for mm in (m.get("metrics") or [])[:3]]
        out.append({
            "node": key,
            "rate": "%.1f msg/s" % (rec["count10"] / 10.0),
            "last": "   ".join(bits) if bits else "none yet",
            "age": "%ds ago" % int((now - rec["lastTs"]) / 1000) if rec["lastTs"] else "--",
        })
    if not out:
        out.append({"node": "--", "rate": "0.0 msg/s", "last": "no DDATA seen yet", "age": "--"})
    return out


def wire_event_rows(limit=60):
    """The EVENTS ring -- births, deaths, commands, STATE, alarm-carrying
    DDATA/NDATA. Newest first, same row shape as the old full log so
    SparkplugWireMsgRow needs no change."""
    store = _wire_store()
    msgs = list(store.get("events") or [])
    msgs.reverse()
    now = _now_millis()
    out = []
    for m in msgs[:limit]:
        bits = [_wire_metric_bit(mm) for mm in (m.get("metrics") or [])[:4]]
        out.append({
            "type": m.get("type", ""), "direction": m.get("direction", ""),
            "node": m.get("node", ""), "device": m.get("device", ""),
            "seq": str(m.get("seq")) if m.get("seq") is not None else "",
            "metrics": "   ".join(bits), "age": "%ds ago" % int((now - m["ts"]) / 1000),
            "error": m.get("error", ""),
        })
    return out


def wire_probe():
    """A tiny read-only summary for `curl`-ing the witness route directly --
    the toolkit's own advice ("give the resource a probe command; a 404 with
    no explanation is the worst way to discover this during a demonstration")."""
    s = wire_summary()
    return {"ok": True, "total": s["total"], "rate": s["rate"], "counts": s["counts"]}


def handle_wire_get(request):
    """Everything the WebDev GET route does -- see handle_wire_post()'s
    docstring for why the real logic lives here and not in doGet.py. Open,
    read-only, no secret: GET carries none, same as the edge observer's own
    contract.

    `?debug=alarms` dumps every row system.alarm.queryStatus() currently
    returns on the hub, SOURCE PATH included -- this is how the real shape
    of a Sparkplug-propagated alarm's source was found live (11/09/2026)
    rather than guessed at from a name. The shape is in the note above ALARMS;
    nothing matches on it any more, which is why no pattern list exists.
    `?debug=events` dumps the EVENTS ring directly, for the same reason.
    `?debug=nodelines` dumps wire_node_lines() itself -- this is how the
    empty "last:" field on the Wire panel was diagnosed live (11/09/2026):
    it found a real, un-truncated multi-KB alarm-object value sitting in
    one node's "last" bit, not the empty string the ship check's screenshot
    suggested.
    """
    try:
        params = request.get("params") or {}
        debug = params.get("debug")
        if debug == "alarms":
            return {"json": {"alarms": _all_cloud_alarms()}}
        if debug == "events":
            return {"json": {"events": wire_event_rows(80)}}
        if debug == "nodelines":
            return {"json": {"nodeLines": wire_node_lines()}}
        if debug == "lastddata":
            only = params.get("type")
            store = _wire_store()
            out = []
            for m in reversed(store.get("messages") or []):
                if m.get("type") in ("DDATA", "NDATA") and (not only or m.get("type") == only):
                    out.append(m)
                if len(out) >= 4:
                    break
            return {"json": {"lastDdata": out}}
        return {"json": wire_probe()}
    except (Exception, JThrowable), e:
        return {"json": {"ok": False, "message": str(e)}}


# --- WebDev handler body (called from doPost.py, which stays byte-0-minimal) --

def handle_wire_post(request):
    """Everything the WebDev POST route does. Kept OUT of doPost.py itself:
    a WebDev handler file may carry nothing but the def and nested code (a
    top-level import there is a silent empty-200, verified in this toolkit's
    own knowledge base) -- so all the real logic, including its own imports,
    lives in an ordinary script module where none of that applies.

    MEASURED LIVE (11/09/2026), against the deployed webdev-gateway-6.3.8
    jar's own PythonResourceHandler.wrapRequest(): when Content-Type is
    application/json, `request['data']` is ALREADY the JSON-decoded object
    (Jython jsonDecode ran inside the handler before this function is ever
    called) -- not a raw string. Re-JSON-decoding it (the first version of
    this function did `system.util.jsonDecode(str(request['data']))`) fed
    jsonDecode a Python repr of a dict ("{u'topic': ...}"), which is not
    valid JSON, silently failed, and fell back to an empty body -- every
    post recorded a real row but with topic '' and type UNKNOWN. `data` is
    also duplicated at `request['postData']` for a POST specifically; both
    exist so a GET-with-body and a POST read the same key.
    """
    import base64
    try:
        body = request.get("data")
        try:
            topic = body.get("topic", "")
            b64 = body.get("payload_b64", "")
        except AttributeError:
            # Content-Type was not application/json (or the listener sent
            # something unexpected), so `data` is the raw string/bytes instead -- parse
            # it ourselves rather than trust the pre-parse.
            text = str(request.get("postData") if request.get("postData") is not None else body or "{}")
            body = system.util.jsonDecode(text)
            topic = body.get("topic", "")
            b64 = body.get("payload_b64", "")
        raw_bytes = base64.b64decode(str(b64)) if b64 else ""
        record_wire(topic, raw_bytes)
        return {"json": {"ok": True}}
    except (Exception, JThrowable), e:
        _logger().warn("wire ingest failed: %s" % _because(e))
        return {"json": {"ok": False, "message": str(e)}}


# --- the roads -- connector lines between the flow-diagram cards --------------

def _node_recent_ddata(edge_index, window_ms=8000):
    """Is this edge's own node actually IN the wire log right now -- the one
    signal nobody can dispute, because it is literally what the WIRE panel
    beside this connector shows. Deliberately NOT the observer's
    transmission.connected, which can read stale or false while that edge's
    data keeps arriving (measured 11/09/2026): this badge would otherwise
    show "stalled" directly next to a wire log full of that same edge's
    DDATA, which is the exact contradiction this fix exists to remove."""
    edge = _edge(edge_index)
    if edge is None:
        return False
    now = _now_millis()
    store = _wire_store()
    for m in reversed(store["messages"]):
        if now - m["ts"] > window_ms:
            break
        if m.get("node") == edge["agent"]:
            return True
    return False


def road_flowing(which):
    if which in ("edge0", "edge1"):
        return _node_recent_ddata(int(which[-1]))
    if which == "wire":
        return wire_summary()["flowing"]
    if which == "return":
        # A real signal now that the EVENTS ring exists: light up for a few
        # seconds after an actual NCMD/DCMD was seen on the wire, which is
        # exactly what a cloud-side write (write_setpoint/write_mode/
        # rebirth_cloud) produces. Never invented -- if nothing wrote
        # anything recently, this stays stalled.
        store = _wire_store()
        now = _now_millis()
        for m in reversed(store["events"]):
            if now - m["ts"] > 6000:
                break
            if m.get("type") in ("NCMD", "DCMD"):
                return True
        return False
    return False


def road_badge(which):
    """One direction of the strip's middle, in words and a badge colour.

    This used to be a pair of animated bars styled by `wd-conn-h` classes. The
    words say what the animation only implied, and the page carries no legend."""
    flowing = road_flowing(which)
    if which == "return":
        if flowing:
            return {"text": u"← commands moving", "badge": "badge-ok",
                    "why": "A command (NCMD or DCMD) crossed the broker in the last few seconds."}
        return {"text": u"← commands idle", "badge": "badge-neutral",
                "why": "No command has crossed the broker recently. Scenario 3 sends one."}
    if flowing:
        return {"text": u"data flowing →", "badge": "badge-ok",
                "why": "The edges are publishing and MQTT Engine is receiving."}
    return {"text": u"data stalled →", "badge": "badge-alarm",
            "why": "Nothing has crossed the broker for more than %d s." % (WIRE_STALE_MS / 1000)}


# --- gateway links ---------------------------------------------------------------
#
# Every link goes through the console's sign-in door (docs/SIGN-IN.md):
# `https://<host>/_wd/login?next=<path>` signs the browser in server-side, the
# same as every other "Open X gateway" button, so nothing here asks for a
# password. Built from this module's own constants rather than wd-control's
# stack list, so it cannot go stale behind demos.json.
#
# The broker has no UI of its own any more: it is MQTT Distributor, a module on
# the hub pair, and its settings are a page in the hub's gateway UI.
# `/app/mqtt-distributor` is the module's own mount (read from the hub's
# /data/app/navigation, 18/09/2026) and was opened signed in through the door
# before it went here. console.test is the master half; its settings are the
# pair's, because redundancy syncs them to the backup.
BROKER_TEXT = ("MQTT Distributor on the hub (ssl://mqtt-master:8883, "
               "fails over to mqtt-backup)")
BROKER_PAGE = "/app/mqtt-distributor"


def links():
    """The Gateways popup's rows: both edges, the hub, and the broker's page."""
    out = []
    for edge in EDGES:
        out.append({
            "stack": edge["host"], "label": "Open " + edge["label"].split(" (")[0] + " gateway",
            "url": "https://%s/_wd/login?next=/web/home" % edge["test_host"],
            "up": True, "signin": True, "creds": False, "note": "opens signed in",
        })
    out.append({
        "stack": "ignition", "label": "Open Cloud gateway (hub)",
        "url": "https://console.test/_wd/login?next=/web/home",
        "up": True, "signin": True, "creds": False, "note": "opens signed in",
    })
    out.append({
        "stack": "ignition", "label": "Open MQTT Distributor settings (hub)",
        "url": "https://console.test/_wd/login?next=" + BROKER_PAGE,
        "up": True, "signin": True, "creds": False, "note": BROKER_TEXT,
    })
    return out


# ------------------------------------------------------------- protobuf decode
#
# Hand-rolled against the Sparkplug B wire format (protobuf: a stream of
# varint-keyed fields), ported from sparkplug-lab/tools/spy.py -- the lab's own
# independent witness for the same protocol. No dependency, ~70 lines, and
# being able to read it is part of what makes this witness trustworthy: it
# decodes the SAME bytes the broker carried, not a claim from either gateway.
#
# Jython 2 strings are byte strings indexed by ord(), unlike a Python 3
# `bytes` object -- the one real port difference from spy.py.

DATATYPE = {
    1: "Int8", 2: "Int16", 3: "Int32", 4: "Int64",
    5: "UInt8", 6: "UInt16", 7: "UInt32", 8: "UInt64",
    9: "Float", 10: "Double", 11: "Boolean", 12: "String",
    13: "DateTime", 14: "Text", 15: "UUID", 16: "DataSet",
    17: "Bytes", 18: "File", 19: "Template",
}


def _varint(buf, i):
    shift, result = 0, 0
    while True:
        b = ord(buf[i])
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf):
    i, n = 0, len(buf)
    out = []
    while i < n:
        key, i = _varint(buf, i)
        fnum, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = _varint(buf, i)
        elif wtype == 1:
            val, i = buf[i:i + 8], i + 8
        elif wtype == 2:
            ln, i = _varint(buf, i)
            val, i = buf[i:i + ln], i + ln
        elif wtype == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError("unsupported wire type %d" % wtype)
        out.append((fnum, wtype, val))
    return out


def _template(buf):
    """A Sparkplug Template VALUE -- repeated Metric metrics = 2, same
    Metric message recursively. MEASURED LIVE 11/09/2026 via the new
    ?debug=lastddata probe: this demo runs Sparkplug Templates
    (convertUdts=false), so EVERY AlarmDemo DDATA metric under Pumps is
    datatype=Template, name="North"/"South" (the UDT INSTANCE, not a
    station tag) -- Level/Inflow/etc. arrive NESTED inside this, one
    Template-valued metric per changed member (report by exception), not
    as their own top-level metrics. Decoding stopped at fnum 10-15
    (plain scalars) never touched fnum 18 at all, so every one of these
    came back with value=None -- not a wrong type, a MISSING one."""
    out = []
    for fnum, wtype, val in _fields(buf):
        if fnum == 2:
            out.append(_metric(val))
    return out


def _dataset(buf):
    """A Sparkplug DataSet VALUE -- num_of_columns=1, columns=2 (repeated
    string), types=3 (repeated uint32 DataType), rows=4 (repeated Row,
    each a repeated DataSetValue). AlarmReconciliation (a node-level
    metric on every NDATA) is one of these; row count alone (0 vs N) is
    the honest, useful summary -- decoding every column type just to
    print it is not worth it for a witness panel."""
    columns, rows = [], 0
    for fnum, wtype, val in _fields(buf):
        if fnum == 2:
            columns.append(val)
        elif fnum == 4:
            rows += 1
    return {"columns": columns, "rows": rows}


def _metric(buf):
    m = {}
    for fnum, wtype, val in _fields(buf):
        if fnum == 1:
            m["name"] = val
        elif fnum == 2:
            m["alias"] = val
        elif fnum == 3:
            m["timestamp"] = val
        elif fnum == 4:
            m["datatype"] = DATATYPE.get(val, "type%d" % val)
        elif fnum == 5:
            m["is_historical"] = bool(val)
        elif fnum == 7:
            # is_null=7, metadata=8 -- this file had them swapped (reading
            # fnum 8 as is_null, which meant a present MetaData submessage
            # -- a non-empty byte string -- was misread as "is_null=True",
            # and real is_null=7 was never read at all).
            m["is_null"] = bool(val)
        elif fnum == 10:
            m["value"] = val               # int_value  (uint32)
        elif fnum == 11:
            m["value"] = val               # long_value (uint64)
        elif fnum == 12:
            m["value"] = round(struct.unpack("<f", val)[0], 6)
        elif fnum == 13:
            m["value"] = struct.unpack("<d", val)[0]
        elif fnum == 14:
            m["value"] = bool(val)
        elif fnum == 16:
            m["value"] = _dataset(val)
        elif fnum == 18:
            m["value"] = _template(val)
        elif fnum == 15:
            m["value"] = val
    dt = m.get("datatype", "")
    v = m.get("value")
    if dt in ("Int8", "Int16", "Int32") and isinstance(v, (int, long)):
        if v >= 1 << 31:
            m["value"] = v - (1 << 32)
    if dt == "Int64" and isinstance(v, (int, long)) and v >= 1 << 63:
        m["value"] = v - (1 << 64)
    return m


def _decode_payload(raw):
    out = {"metrics": []}
    for fnum, wtype, val in _fields(raw):
        if fnum == 1:
            out["timestamp"] = val
        elif fnum == 2:
            out["metrics"].append(_metric(val))
        elif fnum == 3:
            out["seq"] = val
        elif fnum == 4:
            out["uuid"] = val
    return out


# --- history: keep it enabled, and a trend to show the recovered minute -----
# (Phase 5 item 6)
#
# MEASURED LIVE (SPARKPLUG.md T6): after a cut, Transmission's buffered
# values arrive as ONE DDATA carrying 192 historical metrics -- and are
# stored NOWHERE, because Engine only historises a historical metric into a
# tag that already has history enabled, and no AlarmDemo Engine tag did.
# Worse, Engine RECREATES every AlarmDemo tag on each fresh birth, so
# "enabled once" does not stay enabled -- this has to be re-applied on a
# timer the same way sf_demo.ensure_history() is (see
# ignition/timer/SparkplugHistory/handleTimerEvent.py, a sibling of the
# existing SFHistory timer, not a change to it -- these are two different
# demos' tags on the one historian).

HISTORY_PROVIDER = "Postgres"


def ensure_history():
    """Turn on tag history for every AlarmDemo station tag on Engine,
    idempotently -- only tags actually missing it are written. Also covers
    EdgeNotice (under the DEVICE folder, not the site/UDT one -- see
    _engine_notice_path): SPARKPLUG.md C2's own measured finding is that a
    notification raised during a cut is buffered by Transmission and
    flushed as historical DDATA on reconnect, and Engine drops a historical
    metric on arrival unless the DESTINATION tag already has history on --
    so a notification during a cut is lost forever unless this sweep has
    already caught EdgeNotice before the flush lands."""
    configured = 0
    for edge in EDGES:
        folder = _engine_site_folder(edge)
        for spec in TAGS:
            name = spec["name"]
            path = _engine_tag_path(edge, name)
            try:
                if not system.tag.exists(path):
                    continue
                current = system.tag.getConfiguration(path, False)
                if current and current[0].get("historyEnabled"):
                    continue
                system.tag.configure(folder, [{
                    "name": name,
                    "historyEnabled": True,
                    "historyProvider": HISTORY_PROVIDER,
                }], "m")
                configured += 1
            except (Exception, JThrowable), e:
                _logger().warn("could not historise %s: %s" % (path, e))

        device_folder = _engine_device_folder(edge)
        notice_path = _engine_notice_path(edge, "EdgeNotice")
        try:
            if system.tag.exists(notice_path):
                current = system.tag.getConfiguration(notice_path, False)
                if not (current and current[0].get("historyEnabled")):
                    system.tag.configure(device_folder, [{
                        "name": "EdgeNotice",
                        "historyEnabled": True,
                        "historyProvider": HISTORY_PROVIDER,
                    }], "m")
                    configured += 1
        except (Exception, JThrowable), e:
            _logger().warn("could not historise EdgeNotice for %s: %s" % (edge["agent"], e))
    if configured:
        _logger().info("enabled history on %d AlarmDemo tag(s)" % configured)
    return configured


def trend_series(minutes=10):
    """Edge 3 / Edge 4 Level, one source per edge, laid on the same fixed grid
    sf_demo's own trend uses (sf_demo._grid) -- reused rather than
    re-derived, so the outage/jitter rules for "when does a line actually
    break" stay in the one place that already got them right."""
    from java.util import Date
    end = _now_millis()
    start = end - int(minutes) * 60 * 1000
    sources = {}
    for i, edge in enumerate(EDGES):
        key = "edge%d" % (i + 1)
        path = _engine_tag_path(edge, "Level")
        samples = []
        try:
            data = system.tag.queryTagHistory(
                paths=[path], startDate=Date(start), endDate=Date(end),
                returnSize=-1, returnFormat="Wide",
                noInterpolation=True, includeBoundingValues=False)
            for r in range(data.getRowCount()):
                value = data.getValueAt(r, 1)
                if value is None:
                    continue
                samples.append((data.getValueAt(r, 0).getTime(), round(float(value), 2)))
        except (Exception, JThrowable), e:
            _logger().warn("history query failed for %s: %s" % (path, e))
        sources[key] = sf_demo._grid(samples, start, end) if samples else []
    for i in (1, 2):
        sources.setdefault("edge%d" % i, [])
    return sources


# ================================================================================
# THE GUIDED-SCENARIOS PAGE (14/09/2026)
#
# One dict per panel, bound ONCE to that view's custom.d, so a panel costs one
# gateway call per poll and every label on it reads the same moment. The words
# live here, not in the views: a plain name first, the technical term as a
# bracketed hint -- the page is for someone seeing Sparkplug for the first time.
# The views themselves are generated by scripts/gen-sparkplug-views.py.
# ================================================================================

# wd-control holds the cut's own deadline (control/mqttcut.py); these only let
# the page keep a history, and a countdown if wd-control stops answering. A script restart forgets them, and the page then
# falls back to what the wire says, which is still true.
CUT_GLOBAL_KEY = "sparkplug_cut_until"
CUT_HISTORY_KEY = "sparkplug_cut_history"
CUT_INTERVALS_KEY = "sparkplug_cut_intervals"
DOT = u" %s " % unichr(0xb7)

VARIANT_WORDS = {
    "base": "Original layout",
    # Short: each is followed by "(PumpStation), 8 tags" in a 259px heading at
    # 1366x640, and "Changed: a tag's data type" clipped it (23/09/2026).
    "add_member": "Tag added",
    "extra-member": "Tag added",
    "remove_member": "Tag removed",
    "alarm_setpoint": "Alarm limit changed",
    "default_value": "Start value changed",
    "datatype": "Data type changed",
}
EVENT_WORDS = {"active": "became active", "clear": "cleared", "ack": "was acknowledged",
               "test": "test message"}


def _as_long(v):
    try:
        return long(v)
    except (Exception, JThrowable):
        return 0


def _set_cut_until(ms):
    try:
        system.util.getGlobals()[CUT_GLOBAL_KEY] = long(ms)
    except (Exception, JThrowable):
        pass


def _cut_until():
    try:
        return _as_long(system.util.getGlobals().get(CUT_GLOBAL_KEY) or 0)
    except (Exception, JThrowable):
        return 0


def _cut_remaining_s():
    """Seconds left on Edge3's cut. wd-control's deadline is the truth -- it
    survives a restart of this script library and of wd-control itself --
    and the page's own global is only the fallback while wd-control is not
    answering."""
    try:
        import demo_control
        cuts, why = demo_control.mqtt_cuts()
        if not why:
            return int(cuts.get(CUT_STACK, 0))
    except (Exception, JThrowable):
        pass
    left = _cut_until() - _now_millis()
    return int((left + 999) / 1000) if left > 0 else 0


def _record_cut(what, ms=None):
    try:
        g = system.util.getGlobals()
        history = list(g.get(CUT_HISTORY_KEY) or [])
        history.insert(0, {"ts": long(ms or _now_millis()), "what": what})
        g[CUT_HISTORY_KEY] = history[:20]
    except (Exception, JThrowable):
        pass


def _record_cut_start(seconds):
    try:
        g = system.util.getGlobals()
        now = _now_millis()
        spans = list(g.get(CUT_INTERVALS_KEY) or [])
        spans.append({"start": long(now), "end": long(now + int(seconds) * 1000)})
        g[CUT_INTERVALS_KEY] = spans[-20:]
    except (Exception, JThrowable):
        pass


def _record_cut_end():
    try:
        g = system.util.getGlobals()
        now = _now_millis()
        spans = list(g.get(CUT_INTERVALS_KEY) or [])
        if spans and spans[-1]["end"] > now:
            spans[-1] = {"start": spans[-1]["start"], "end": long(now)}
            g[CUT_INTERVALS_KEY] = spans
    except (Exception, JThrowable):
        pass


def _cut_spans():
    try:
        return list(system.util.getGlobals().get(CUT_INTERVALS_KEY) or [])
    except (Exception, JThrowable):
        return []


def _check_cut_expiry():
    """A cut that ran out records itself once, the first time anyone looks."""
    until = _cut_until()
    if until and until <= _now_millis():
        _set_cut_until(0)
        _record_cut("Came back by itself when the 60 s ran out", until)


def _short_name(edge):
    return "Edge %s" % edge["agent"][-1]


def _who(node):
    """One name per edge, everywhere: "Edge 3". The station's own tag name
    (North/South) stays in tag PATHS -- it is part of the tag tree on both
    sides -- but it is never the edge's name in the page."""
    node = str(node or "")
    for edge in EDGES:
        if edge["agent"] == node:
            return edge["label"]
    return node or "--"


def _badge(level):
    return {"good": "badge-ok", "warn": "badge-warn", "bad": "badge-alarm"}.get(level, "badge-neutral")


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _age_text(ms):
    if ms is None:
        return "none yet"
    s = int(ms / 1000)
    if s < 2:
        return "just now"
    if s < 120:
        return "%d s ago" % s
    return "%d min ago" % (s // 60)


def _when(ms):
    """One format per column: "today 11:52:54", or "12/09 18:17:02" on any other day."""
    ms = _as_long(ms)
    if not ms:
        return "--"
    try:
        d = system.date.fromMillis(ms)
        if system.date.format(d, "yyyyMMdd") == system.date.format(system.date.now(), "yyyyMMdd"):
            return "today " + system.date.format(d, "HH:mm:ss")
        return system.date.format(d, "dd/MM HH:mm:ss")
    except (Exception, JThrowable):
        return "--"


def _clock(ms):
    """HH:mm:ss -- for a time beside the row it explains, minutes old at most."""
    ms = _as_long(ms)
    if not ms:
        return DASH
    try:
        return system.date.format(system.date.fromMillis(ms), "HH:mm:ss")
    except (Exception, JThrowable):
        return DASH


# The one wording for "what you are looking at is not current", used by scenario
# 2's per-edge note and scenario 4's alarm table so the two panels agree.
AS_AT = u"cloud rows as at %s"


def _as_of(edge_index):
    """Are this edge's CLOUD rows current, and what time are they as at?

    Only the cloud half goes stale in a cut: the edge's own column comes from
    its observer, which answers over the container network and not the broker,
    so it stays live throughout (measured 22/09/2026 -- the edge kept
    acknowledging and raising events the cloud never saw). The note says cloud
    for that reason.
    """
    current = _edge_live(edge_index)
    ms = _last_heard_ms(edge_index)
    tail = (AS_AT % _clock(ms)) if ms else u"nothing heard from it yet"
    return {"current": current, "asOf": _clock(ms), "tail": tail,
            "note": u"" if current else (u"Offline %s %s" % (DASH, tail))}


def _node_rate(edge_index):
    edge = _edge(edge_index)
    now = _now_millis()
    n = 0
    for m in _wire_store()["messages"]:
        if m.get("node") == edge["agent"] and m.get("type") in ("DDATA", "NDATA") \
                and now - m["ts"] <= 10000:
            n += 1
    return n / 10.0


def _edge_link(edge_index):
    """Connected / Cut / Offline -- one word, for the system strip."""
    _check_cut_expiry()
    if edge_index == CUT_EDGE_INDEX and _cut_remaining_s() > 0:
        return "Cut", "warn"
    state, _err = _edge_state(edge_index)
    if state is None or _engine_node_online(edge_index) is False:
        return "Offline", "bad"
    age = _wire_last_seen_age_ms(edge_index)
    if age is not None and age > WIRE_STALE_MS:
        return "Cut", "warn"
    return "Connected", "good"


DASH = unichr(0x2014)

TAG_LABELS = dict((t["name"], t["label"]) for t in TAGS)


def _display_member(name):
    """A tag's name as the rest of the page spells it: "Discharge Pressure"."""
    name = str(name)
    if name in TAG_LABELS:
        return TAG_LABELS[name]
    out = ""
    for i, ch in enumerate(name):
        if i and ch.isupper() and not name[i - 1].isupper():
            out += " "
        out += ch
    return out


def _event_words(n):
    """The same words for one notification event, whichever route shows it."""
    event = str(n.get("event") or "").lower()
    alarm = str(n.get("alarm") or "").replace(" (cloud)", "")
    if event == "test" or not alarm or alarm == "manual test":
        return "Test notification"
    return "%s %s" % (alarm, EVENT_WORDS.get(event, "changed"))


def _mqtt_text(n):
    published = n.get("published") or {}
    if published.get("ok"):
        return ("%s ms" % published.get("ms")) if published.get("ms") is not None else "sent"
    if published.get("pending"):
        return "on its way"
    if published.get("error"):
        return "failed"
    return DASH


def _route_lines(edge_index):
    """Both routes describe the SAME event -- the edge's latest -- in the same
    words, and differ only in whether (and how fast) that event arrived. The
    edge sends one document with one `ts` down both routes, so `ts` matches them."""
    state, _err = _edge_state(edge_index)
    notes = sorted(((state or {}).get("notifications") or []), key=lambda n: -_as_long(n.get("ts")))
    if not notes:
        return "Nothing sent yet", "Nothing sent yet"
    n = notes[0]
    ts = _as_long(n.get("ts"))
    # No "today": the line is one cell wide at 1366x640 and clipped its last
    # word with it (23/09/2026). An older event keeps its date.
    line = "%s%s%s" % (_event_words(n), DOT, _when(ts).replace("today ", ""))
    spk = "not in the cloud yet"
    e = _engine_notice(edge_index, "EdgeNotice")
    if e.get("raw"):
        try:
            doc = system.util.jsonDecode(e["raw"])
            if isinstance(doc, dict) and _as_long(doc.get("ts")) >= ts:
                spk = "in the cloud"
        except (Exception, JThrowable):
            pass
    raw = "not received"
    g = raw_notify_grouped(edge_index)
    if not g["empty"] and g["eventTs"] is not None and _as_long(g["eventTs"]) >= ts:
        if _as_long(g["eventTs"]) == ts and g["latencyMs"] is not None:
            raw = "arrived in %d ms" % g["latencyMs"]
        else:
            raw = "in the cloud"
    return line + DOT + spk, line + DOT + raw


UDT_ROW = 20


def _udt_height(rows):
    """The list box fits its rows exactly -- no empty area inside it."""
    return "%dpx" % (max(len(rows), 1) * UDT_ROW + 2)


def _alarm_phrase_end(active, acked, n):
    """One unambiguous state for one end of an alarm (review 4): the newest event's
    state, and how many of that alarm's events are still not acknowledged."""
    # Ignition's own words for the states, and the count bare: "Cleared, not
    # acknowledged (2)" clipped its ")" in the 167px cell at 1366x640
    # (23/09/2026). The row's tooltip says what the count counts.
    if active:
        if not acked:
            return ("Active, unacknowledged" + ((" (%d)" % n) if n > 1 else ""), "badge-alarm")
        return ("Active, acknowledged" + ((" (+%d)" % n) if n else ""), "badge-alarm")
    if n:
        return ("Cleared, unacknowledged (%d)" % n, "badge-neutral")
    return ("Cleared", "badge-neutral")


def _last_sent(edge_index):
    """The last setpoint and mode the cloud actually sent this edge, from the command history."""
    agent = _edge(edge_index)["agent"]
    sp, mode = DASH, DASH
    for m in reversed(_wire_store().get("commands") or []):
        if m.get("node") != agent or m.get("type") != "DCMD":
            continue
        for x in (m.get("metrics") or []):
            name = str(x.get("name", ""))
            if sp == DASH and name.endswith("LevelSetpoint"):
                try:
                    sp = "%.1f%%" % float(x.get("value"))
                except (Exception, JThrowable):
                    sp = str(x.get("value"))
            if mode == DASH and name.endswith("Mode"):
                mode = str(x.get("value"))
        if sp != DASH and mode != DASH:
            break
    return sp, mode


KIND_WORDS = {"NBIRTH": "Startup", "DBIRTH": "Startup", "NDATA": "Value update", "DDATA": "Value update",
              "NCMD": "Command", "DCMD": "Command", "NDEATH": "Shutdown", "DDEATH": "Shutdown",
              "STATE": "Cloud status", "NOTIFY": "Plain MQTT notification"}


_READY_CACHE = {"at": 0, "note": ""}
READY_TTL_MS = 2000


def not_ready():
    """Why this demo cannot be driven yet, in plain words, or "".

    The MQTT tab is reachable whether or not the demo is running, so that its
    page can be read before anyone decides to start it (demo_control.tab_state).
    That makes this the one thing standing between a customer and a page of
    dashes with live buttons on it: the banner says what is wrong, and every
    action button on every scenario is bound to it.

    Memoised for READY_TTL_MS because five panels and the strip all ask on the
    same tick. Imported lazily and never allowed to raise, like every other
    guard in the console: a transform that throws renders its component as a red
    ERROR box and logs nowhere a script can see it -- which on this page would
    put an error box exactly where the explanation should be.
    """
    now = _now_millis()
    if now - _READY_CACHE["at"] < READY_TTL_MS:
        return _READY_CACHE["note"]
    note = ""
    try:
        import demo_control
        note = demo_control.ready_note("sparkplug") or ""
    except (Exception, JThrowable), e:
        _logger().warn("readiness unavailable: %s" % _because(e))
        note = ""
    _READY_CACHE["at"] = now
    _READY_CACHE["note"] = note
    return note


def page_strip():
    edges = []
    live = 0
    for i, edge in enumerate(EDGES):
        status, level = _edge_link(i)
        if _edge_live(i):
            live += 1
        state, _err = _edge_state(i)
        # The same rows the Alarms panel shows, so the two counts always agree.
        active = len([r for r in _alarm_view_rows(i) if r["edgeActive"]]) if state else None
        if active is None:
            alarms = "no reply"
        elif active == 0:
            alarms = "no active alarms"
        else:
            alarms = "%s active" % _plural(active, "alarm")
        edges.append({
            "name": edge["label"], "status": status, "badge": _badge(level), "alarms": alarms,
            "rate": "%.1f msg/s" % _node_rate(i),
        })
    w = wire_summary()
    total = len(EDGES)
    if live == total:
        cloud_status, cloud_level = "Receiving", "good"
    elif live:
        cloud_status, cloud_level = "Partial", "warn"
    else:
        cloud_status, cloud_level = "No data", "bad"
    note = not_ready()
    rate = "%.1f msg/s" % w["rate"]

    # StripNode's parameters (views/StripNode, shared with the Store & Forward
    # strip). A demo that is not running draws its boxes with no state and a
    # dash: MQTT Engine keeps an edge's last reading long after the edge has
    # gone, and "Offline" in red under a banner saying "not running" is the
    # same stopped-read-as-broken the Redundancy tab had.
    def node(name, state, level, line, tip=""):
        if note:
            return {"name": name, "state": "", "badge": "badge-neutral",
                    "line": DASH, "tip": tip}
        return {"name": name, "state": state, "badge": _badge(level),
                "line": line, "tip": tip}
    nodes = {}
    for i, e in enumerate(edges):
        nodes["n%d" % i] = node(e["name"], e["status"], {"badge-ok": "good", "badge-alarm": "bad",
                                                          "badge-warn": "warn"}.get(e["badge"], "neutral"),
                                e["rate"] + DOT + e["alarms"])
    return {
        "e0": edges[0], "e1": edges[1],
        "n0": nodes["n0"], "n1": nodes["n1"],
        "brokerNode": {"name": "MQTT broker", "state": "", "badge": "badge-neutral",
                       "line": "Distributor, on the hub",
                       "tip": "MQTT Distributor runs on the hub pair and fails over with it"},
        "cloudNode": node("Cloud gateway (hub)", cloud_status, cloud_level, "MQTT Engine",
                          "the hub, reading both edges through MQTT Engine"),
        "cloud": {"status": cloud_status, "badge": _badge(cloud_level),
                  "edges": "%d of %d edges live" % (live, total),
                  "live": live, "total": total, "rate": rate},
        # The lanes too: "data stalled" in red is a stopped demo read as a
        # broken one, right beside a banner saying it was never started.
        "dataLane": road_badge("wire") if not note else
        {"text": DASH, "badge": "badge-neutral", "why": note},
        "cmdLane": road_badge("return") if not note else
        {"text": DASH, "badge": "badge-neutral", "why": note},
        "notReady": note,
    }


def ctl(scenario):
    """The rail's buttons for one scenario, guarded -- sparkplug's side of
    demo_action's `can` contract. Keyed by verb + edge index, the same names
    the SparkplugCtl* views bind and demo_rail.ACTIONS fires.

    Never raises: this is bound to a view, where a transform that throws draws
    a red ERROR box and logs nothing.
    """
    import demo_action

    try:
        note = not_ready()

        def can(label, ok=True, why=""):
            if note:
                return demo_action.can(False, note, label, note="")
            return demo_action.can(ok, why, label)

        if scenario == "alarms":
            out = {}
            for i in (0, 1):
                out["trip%d" % i] = can("Trip")
                out["reset%d" % i] = can("Reset")
                out["ackEdge%d" % i] = can("Ack edge")
                out["ackCloud%d" % i] = can("Ack cloud")
        elif scenario == "commands":
            out = {}
            for i in (0, 1):
                out["send%d" % i] = can("Send")
                out["auto%d" % i] = can("Auto")
                out["manual%d" % i] = can("Manual")
        elif scenario == "outage":
            _check_cut_expiry()
            out = _outage_can(_cut_remaining_s())
        elif scenario == "udt":
            out = {"diverge": can("Diverge"), "putback": can("Put back"),
                   "v2edge3": can("Edge 3 v2"), "v2edge4": can("Edge 4 v2"),
                   "retire": can("Retire v1"), "reset": can("Reset")}
        elif scenario == "notify":
            out = {"trip0": can("Trip"), "trip1": can("Trip")}
        else:
            out = {}
        return {"ready": not note, "can": out}
    except (Exception, JThrowable), e:
        _logger().warn("ctl(%s) failed: %s" % (scenario, _because(e)))
        return {"ready": False, "can": {}}


# --- 1 Live data ----------------------------------------------------------------

def _plain_value(spec, value):
    if value is None:
        return DASH
    name = spec["name"]
    if name == "PumpRunning":
        return "Running" if value else "Stopped"
    if name == "PumpFault":
        return "Fault" if value else "OK"
    if name == "Heartbeat":
        try:
            return "%d" % int(value)
        except (Exception, JThrowable):
            return str(value)
    if spec.get("boolean"):
        return "On" if value else "Off"
    if isinstance(value, (int, long, float)) and not isinstance(value, bool):
        return ("%.1f" % value) + (spec.get("unit") or "")
    return str(value) + (spec.get("unit") or "")


def _live_rows(edge_index):
    """The CLOUD is read first and the edge second, fresh: the edge copy is then
    never older than the cloud copy, so the cloud can never look AHEAD of the
    edge (a cached edge read could, by up to STATE_TTL_MS).

    "Behind edge" means one thing: Engine's measured network delay for this
    node, always in ms. Two values can still differ by one publish (~1 s) while
    a change travels -- the Watch hint says so rather than a second meaning here."""
    edge = _edge(edge_index)
    paths = [_engine_tag_path(edge, spec["name"]) for spec in TAGS]
    try:
        qvs = system.tag.readBlocking(paths)
    except (Exception, JThrowable):
        qvs = [None] * len(paths)
    state, err = _observer_get(edge, "state")
    _STATE_CACHE[edge_index] = {"at": _now_millis(), "state": state, "err": err}
    edge_tags = {}
    for t in ((state or {}).get("tags") or []):
        edge_tags[str(t.get("path", "")).split("/")[-1]] = t
    lat = _engine_data_latency_ms(edge_index)
    delay = ("%d ms" % int(lat)) if lat is not None else DASH
    rows = []
    for spec, qv in zip(TAGS, qvs):
        c_val = qv.value if (qv is not None and qv.value is not None) else None
        et = edge_tags.get(spec["name"], {})
        e_val = et.get("value")
        rows.append({
            "name": spec["label"], "edge": _plain_value(spec, e_val), "cloud": _plain_value(spec, c_val),
            "delay": delay,
            "tip": "At the edge: %s (changed %s)\nIn the cloud: %s" % (
                _plain_value(spec, e_val), _fmt_ts(et.get("ts")), _plain_value(spec, c_val)),
        })
    return rows, delay


def page_live():
    """One row per tag: each edge's value at the edge, in the cloud, and the delay.

    The delay used to be a sentence in the header of a SECOND list of the same
    updates. It is a number about a tag, so it is a column beside the tag.

    With the demo stopped, every cell is a dash. MQTT Engine KEEPS the last
    value it received in its tag provider, so the cloud columns otherwise go on
    showing 26.1% and "6 ms" beside an edge reading "no reply" -- a page that
    says a stopped demo is not running while apparently displaying its live
    data. A reviewer read exactly that as "half working" (21/09/2026)."""
    dash = DASH
    if not_ready():
        return {"tags": [{"name": spec["label"], "e3edge": dash, "e3cloud": dash, "e3delay": dash,
                          "e4edge": dash, "e4cloud": dash, "e4delay": dash,
                          "tip": "Nothing to read: the demo is not running."}
                         for spec in TAGS],
                "ready": False}
    rows0, _delay0 = _live_rows(0)
    rows1, _delay1 = _live_rows(1)
    tags = []
    for a, b in zip(rows0, rows1):
        tags.append({"name": a["name"],
                     "e3edge": a["edge"], "e3cloud": a["cloud"], "e3delay": a["delay"],
                     "e4edge": b["edge"], "e4cloud": b["cloud"], "e4delay": b["delay"],
                     "tip": "Edge 3: %s\nEdge 4: %s" % (a["tip"], b["tip"])})
    return {"tags": tags, "ready": True}


def page_trend(minutes=10, tag="Level"):
    """One tag for both edges, from Engine's history, on a 5 s grid.

    The line breaks ONLY inside an outage this page actually caused: a cut
    recorded by the Cut button, up to Restore or its expiry. Every other hole is
    bridged -- the historian stores changes, so 30-60 s between samples is
    normal, and breaking on those read as constant outages. The chart's
    baseInterval is 5 s, so a missing point is the only thing that breaks it."""
    from java.util import Date
    end = _now_millis()
    start = end - int(minutes) * 60 * 1000
    sources = {}
    for i, edge in enumerate(EDGES):
        samples = []
        try:
            data = system.tag.queryTagHistory(
                paths=[_engine_tag_path(edge, tag)], startDate=Date(start), endDate=Date(end),
                returnSize=-1, returnFormat="Wide", noInterpolation=True, includeBoundingValues=False)
            for r in range(data.getRowCount()):
                value = data.getValueAt(r, 1)
                if value is not None:
                    samples.append((data.getValueAt(r, 0).getTime(), float(value)))
        except (Exception, JThrowable), e:
            _logger().warn("history query failed for %s %s: %s" % (edge["agent"], tag, e))
        spans = _cut_spans() if i == CUT_EDGE_INDEX else []
        hold = None
        try:
            qv = system.tag.readBlocking([_engine_tag_path(edge, tag)])[0]
            hold = float(qv.value) if qv is not None and qv.value is not None else None
        except (Exception, JThrowable):
            hold = None
        sources["edge%d" % (i + 1)] = _trend_points(samples, start, end, spans, hold)
    return sources


TREND_BUCKET_MS = 5000


def _trend_points(samples, start, end, spans=None, hold=None):
    """Samples -> one point per TREND_BUCKET_MS.

    The historian stores CHANGES, so a steady value leaves no samples at all:
    each recorded value is carried forward until the next change (and to now),
    and a window with no change in it is drawn flat at `hold`, the tag's value
    now. Nothing is drawn before the first change inside the window -- a
    bounding value from before it showed Edge 4's setpoint at 62% for ten
    minutes when it had been 50% for 48. Only a recorded cut breaks the line."""
    total = int((end - start) // TREND_BUCKET_MS) + 1
    values = [None] * total
    first_known = None
    for stamp, value in sorted(samples):
        k = int((stamp - start) // TREND_BUCKET_MS)
        if k < 0:
            continue
        if k < total:
            values[k] = value
    # Nothing different recorded in the window, and the value now is the same: the
    # value has been steady, so it is drawn across the whole window. (For a window
    # with no change the historian returns one row at its end, which on its own
    # drew a single point and a 5-second axis.)
    recorded = [v for v in values if v is not None]
    if first_known is None and hold is not None and all(abs(v - hold) < 0.01 for v in recorded):
        first_known = hold
    last = first_known
    for k in range(total):
        bucket_start = start + k * TREND_BUCKET_MS
        in_cut = [sp for sp in (spans or []) if sp["start"] < bucket_start + TREND_BUCKET_MS and sp["end"] > bucket_start]
        if in_cut:
            values[k] = None
            last = None
            continue
        if values[k] is None:
            values[k] = last
        else:
            last = values[k]
    points = []
    for k in range(total):
        if values[k] is None:
            continue
        points.append({"t": system.date.format(system.date.fromMillis(start + k * TREND_BUCKET_MS),
                                               "yyyy-MM-dd HH:mm:ss"),
                       "v": round(values[k], 2)})
    return points


# --- 2 Alarms -------------------------------------------------------------------


def _alarm_view_rows(edge_index):
    """Every firing of an alarm is its own event, acknowledged on its own. Each end
    shows the NEWEST event's state and, where any are left, how many of that alarm's
    events (distinct ids) are not acknowledged. Measured 14/09/2026: Edge 3 held 5
    cleared, unacknowledged events per level alarm -- the same 15 ids on the edge and
    the hub -- while its log showed OTHER firings of those alarms acknowledged."""
    state, _err = _edge_state(edge_index)
    current = _edge_live(edge_index)
    events = {}
    for a in ((state or {}).get("alarms") or []):
        events.setdefault(a.get("name", ""), []).append(a)
    cloud_by_id = _cloud_alarms_by_id()
    key = "edge_node_id:%s:" % _edge(edge_index)["agent"]
    cloud_unacked = {}
    for r in _all_cloud_alarms():
        if key in str(r.get("source", "")) and r.get("id") and not r.get("acked"):
            cloud_unacked.setdefault(r.get("name"), set()).add(r["id"])
    out = []
    for spec in ALARMS:
        name = spec["name"]
        evs = sorted(events.get(name, []), key=lambda a: -_as_long(a.get("activeTs")))
        newest = evs[0] if evs else None
        unacked = [a for a in evs if not a.get("acked")]
        edge_n = len(set([a.get("id") for a in unacked]))
        cloud_n = len(cloud_unacked.get(name, set()))
        if newest is None:
            edge_end = cloud_end = ("Never fired", "badge-neutral")
        else:
            edge_active = str(newest.get("state", "")).startswith("Active")
            edge_end = _alarm_phrase_end(edge_active, bool(newest.get("acked")), edge_n)
            ca = cloud_by_id.get(newest.get("id"))
            if ca:
                cloud_state = str(ca.get("state", ""))
                cloud_end = _alarm_phrase_end(bool(ca.get("active")) or cloud_state.startswith("Active"),
                                              ", Acknowledged" in cloud_state, cloud_n)
            elif not edge_active and newest.get("acked"):
                # The hub drops an event once it is cleared AND acknowledged.
                cloud_end = _alarm_phrase_end(False, True, cloud_n)
            else:
                cloud_end = ("Not received", "badge-neutral")
        fired = ", ".join([_when(a.get("activeTs")) for a in unacked][:6])
        out.append({
            "name": name, "priority": spec["priority"],
            "edgeText": edge_end[0], "edgeBadge": edge_end[1],
            "cloudText": cloud_end[0],
            # An offline edge's CLOUD cell is greyed: it is the last state that
            # crossed the broker, not the state now, and the block's own note
            # says what time it is as at. The EDGE cell keeps its colour --
            # that half really is live (see _as_of).
            "cloudBadge": cloud_end[1] if current else "badge-neutral",
            # ...and dimmed on top: badge-neutral alone is also a live
            # "Cleared", so a block of cleared rows did not change at all when
            # its edge went offline (23/09/2026).
            "cloudStale": not current,
            "edgeActive": edge_end[0].startswith("Active"),
            "current": current,
            "tip": ("%s, %s priority. Events not acknowledged fired %s" % (name, spec["priority"], fired)) if fired
                   else "%s, %s priority. Every event acknowledged." % (name, spec["priority"]),
        })
    out.append(_link_alarm_row(edge_index))
    return out


# The cloud's OWN alarm on Engine's Node Info/Online, written by
# scripts/sparkplug-setup.sh (docs/SPARKPLUG.md, "The reference setup"). It is
# the answer to "during the outage the cloud's alarm rows are stale and nothing
# marks them": the marking above is the screen's half, this is the gateway's.
def _link_alarm_name(edge):
    return "%s offline" % _short_name(edge)


def _link_alarm_row(edge_index):
    """The link alarm as one more row of the same table -- no edge cell, because
    the edge has no such alarm: it is the CLOUD noticing the edge is gone, and it
    is the one row on the panel that is right during an outage.

    Matched on the reference tag's own PATH, not on the alarm's name: the name
    is written by sparkplug-setup.sh, and a row matched by name would go
    silently dead the day that script spelled it differently. The name shown is
    the gateway's own whenever there is a row to read it from."""
    edge = _edge(edge_index)
    name = _link_alarm_name(edge)
    marker = "%s/%s/Online:" % (CLOUD_FOLDER, edge["agent"])
    active = False
    seen = False
    for r in _all_cloud_alarms():
        if marker not in str(r.get("source", "")):
            continue
        seen = True
        name = str(r.get("name") or name)
        if r.get("active"):
            active = True
    text, badge = ("Active", "badge-alarm") if active else ("Cleared", "badge-neutral")
    return {
        "name": name, "priority": LINK_ALARM_PRIORITY,
        "edgeText": DASH, "edgeBadge": "badge-neutral",
        "cloudText": text, "cloudBadge": badge, "cloudStale": False,
        # Never counted as an active alarm on the strip: it is about the link,
        # not the pump station.
        "edgeActive": False, "current": True,
        "tip": "%s, %s priority. The cloud's own alarm on Engine's Node Info/Online%s -- "
               "the edge has none, and this row is current during an outage."
               % (name, LINK_ALARM_PRIORITY, "" if seen else ", never fired yet"),
    }


def _edge_event_rows(edge_index, limit=20):
    """The edge's own notification pipeline, in plain words, newest first."""
    state, _err = _edge_state(edge_index)
    notes = sorted(((state or {}).get("notifications") or []), key=lambda n: -_as_long(n.get("ts")))
    rows = []
    for n in notes[:limit]:
        # No "· High priority" on every row: an alarm's priority never changes,
        # so it was the same two words on each of its rows (text budget).
        what = _event_words(n)
        rows.append({"ts": _when(n.get("ts")), "tsms": _as_long(n.get("ts")),
                     "who": _edge(edge_index)["label"], "what": what, "mqtt": _mqtt_text(n)})
    return rows


def page_alarms():
    """Both edges' alarm tables, and ONE recent list holding both, newest first.

    Two lists said "every firing is its own event" twice and halved the rows
    each could show; interleaving them is also the only way to see that a
    firing on one edge did not disturb the other."""
    recent = []
    for i in range(len(EDGES)):
        recent.extend(_edge_event_rows(i))
    recent.sort(key=lambda r: -r["tsms"])
    return {"e0": _alarm_view_rows(0), "e1": _alarm_view_rows(1),
            "asOf0": _as_of(0), "asOf1": _as_of(1),
            "recent": recent, "recentCount": len(recent),
            "ready": not not_ready()}


def ack_cloud_all(edge_index):
    """Acknowledge, in the CLOUD, every unacknowledged alarm of one edge."""
    # Every unacknowledged EVENT of this node, not _alarm_view_rows()' one id
    # per alarm name: a manual-ack alarm that re-trips keeps each cleared event
    # (edge4 held five Level High at once, 14/09/2026), so acking one per name
    # left the rest "Cleared, Unacknowledged" and the ack looked lost.
    node = ":/edge_node_id:%s:" % _edge(edge_index)["agent"]
    ids = []
    for r in _all_cloud_alarms():
        if (r.get("id") and r["id"] not in ids and node in r.get("source", "")
                and r.get("name") != CLOUD_OWN_ALARM_NAME and not r.get("acked")):
            ids.append(r["id"])
    name = _short_name(_edge(edge_index))
    if not ids:
        return "Nothing to acknowledge in the cloud for %s." % name
    try:
        system.alarm.acknowledge(ids, "Acknowledged in the cloud from the Sparkplug demo console", ACK_USER)
    except (Exception, JThrowable), e:
        return "Could not acknowledge: %s" % _because(e)
    return "Acknowledged %s for %s in the cloud." % (_plural(len(ids), "alarm"), name)


# --- 3 Cloud to edge --------------------------------------------------------------


def page_commands():
    out = {}
    # Same reason as page_live(): Engine's copy of the setpoint and the mode
    # outlive the edge that sent them, and a stopped demo must not show them
    # under "In the cloud" as though a command had just landed.
    if not_ready():
        blank = {"spEdge": DASH, "spCloud": DASH, "modeEdge": DASH, "modeCloud": DASH,
                 "askedSp": DASH, "askedMode": DASH}
        return {"e0": dict(blank), "e1": dict(blank), "ready": False}
    for i in range(len(EDGES)):
        rows = dict((r["name"], r) for r in tag_rows(i))
        sp = rows.get("Level Setpoint", {})
        mode = rows.get("Mode", {})
        asked_sp, asked_mode = _last_sent(i)
        out["e%d" % i] = {
            "spEdge": sp.get("edgeValue", DASH), "spCloud": sp.get("cloudValue", DASH),
            "modeEdge": mode.get("edgeValue", DASH), "modeCloud": mode.get("cloudValue", DASH),
            "askedSp": asked_sp, "askedMode": asked_mode,
        }
    # No recent-commands list: the MQTT traffic drawer's Commands rows are the
    # same events, in the same words, from the same ring.
    out["ready"] = True
    return out


# --- 4 Link outage -----------------------------------------------------------------

def page_outage():
    _check_cut_expiry()
    i = CUT_EDGE_INDEX
    left = _cut_remaining_s()
    age = _wire_last_seen_age_ms(i)
    stale = age is not None and age > WIRE_STALE_MS
    if left > 0:
        cut = {"text": "Cut, back in %d s" % left, "badge": "badge-warn"}
    elif stale:
        cut = {"text": "Not sending", "badge": "badge-warn"}
    else:
        cut = {"text": "Connected", "badge": "badge-ok"}
    online = _engine_node_online(i)
    engine = {True: ("Online", "good"), False: ("Offline", "bad")}.get(online, ("Unknown", "warn"))
    # The edge's own "connected" flag lags a cut by ~18 s (its keepalive), so the
    # cut/stale test above decides; the observer only says whether it answers.
    state, _err = _edge_state(i)
    if state is None:
        edge_says = ("Not answering", "bad")
    elif left > 0 or stale:
        edge_says = ("Running, holding its data", "warn")
    else:
        edge_says = ("Sending live", "good")
    try:
        cuts = list(system.util.getGlobals().get(CUT_HISTORY_KEY) or [])
    except (Exception, JThrowable):
        cuts = []
    # The link's history: the page's own cuts and restores, plus Edge 3's startups
    # and shutdowns as they crossed the broker.
    agent = _edge(i)["agent"]
    history = [{"tsms": _as_long(h.get("ts")), "what": h.get("what", "")} for h in cuts]
    for m in (_wire_store().get("events") or []):
        if m.get("node") == agent and m.get("type") in ("NBIRTH", "NDEATH"):
            history.append({"tsms": _as_long(m.get("ts")), "what": _event_plain(m)})
    history.sort(key=lambda h: -h["tsms"])
    history = [{"ts": _when(h["tsms"]), "what": h["what"]} for h in history[:40]]
    return {
        "cut": cut,
        "can": _outage_can(left),
        "engine": {"text": engine[0], "badge": _badge(engine[1])},
        "wire": {"text": "Last message %s" % _age_text(age), "badge": _badge("warn" if stale else "good")},
        "edge": {"text": edge_says[0], "badge": _badge(edge_says[1])},
        # No "lesson" paragraph: the scenario's one line says the cloud goes
        # Offline ~15 s after the messages stop, and these four rows show it.
        "history": history, "historyCount": len(history),
        "alarms": _outage_alarm_rows(i),
        # The same marking as scenario 2, in the card's own title rather than a
        # fifth row: the two panels say the same thing in the same words.
        "alarmsTitle": _outage_alarm_title(i),
    }


OUTAGE_ALARM_TITLE = "Alarms during the outage"


def _outage_alarm_title(edge_index):
    a = _as_of(edge_index)
    if a["current"]:
        return OUTAGE_ALARM_TITLE
    return u"%s %s %s" % (OUTAGE_ALARM_TITLE, DASH, a["tail"])


def _outage_alarm_rows(edge_index):
    """Each alarm at both ends, beside the quality of the cloud tag it sits on.
    Measured 22/09/2026 (SPARKPLUG.md, Three field questions, A): during an
    outage the cloud's alarm rows keep their last state while the tag under
    them goes Bad_Stale; the edge's birth puts the rows right."""
    if not_ready():
        # A stopped edge has no alarms to compare, and Engine's copies are its last word.
        return [{"alarm": spec["name"], "edge": DASH, "cloud": DASH, "quality": DASH} for spec in ALARMS]
    edge = _edge(edge_index)
    rows = _alarm_view_rows(edge_index)
    try:
        qualities = [str(q.quality) for q in
                     system.tag.readBlocking([_engine_tag_path(edge, a["tag"]) for a in ALARMS])]
    except (Exception, JThrowable):
        qualities = [DASH] * len(ALARMS)
    def short(text):
        # The Alarms scenario carries the counts; this table has a quarter of its width.
        for head, words in (("Active, unack", "Active, not acked"), ("Active", "Active, acked"),
                            ("Cleared, unack", "Cleared, not acked")):
            if text.startswith(head):
                return words
        return text
    # ALARMS only, so the link alarm _alarm_view_rows() adds at the end stays
    # out: this table is the station's alarms beside the quality of the cloud
    # tag each one sits on, and the link alarm sits on no station tag.
    return [{"alarm": spec["name"], "edge": short(row["edgeText"]), "cloud": short(row["cloudText"]),
             "quality": q} for spec, row, q in zip(ALARMS, rows[:len(ALARMS)], qualities)]


def _outage_can(left):
    """The Cut and Restore buttons' guards, from the same reading as the card:
    a Cut that would be refused is not offered, and the time left is on it."""
    import demo_action
    import demo_control
    try:
        _cuts, unknown = demo_control.mqtt_cuts()
    except (Exception, JThrowable), e:
        unknown = str(e)[:80]
    cut_label = "Cut Edge 3 link (60s)"
    stopped = not_ready()
    if stopped:
        return {"cut": demo_action.can(False, stopped, label=cut_label, note=""),
                "restore": demo_action.can(False, stopped, label="Restore", note=""),
                "rebirth": demo_action.can(False, stopped, label="Rebirth Edge 3", note="")}
    if unknown:
        cut = demo_action.can(False, str(unknown)[:60], label=cut_label)
    elif left > 0:
        cut = demo_action.can(False, "already cut -- back in %ds" % left,
                              label="Cut - %ds left" % left)
    else:
        cut = demo_action.can(True, label=cut_label)
    restore = demo_action.can(left > 0, "nothing is cut off", label="Restore")
    rebirth = demo_action.can(left <= 0, "Edge 3 is cut off -- Restore first", label="Rebirth Edge 3")
    return {"cut": cut, "restore": restore, "rebirth": rebirth}


# --- 5 UDT experiment ----------------------------------------------------------------


def _member_rows(raw, other_names):
    rows = []
    for m in (raw.get("members") or []):
        name = str(m.get("name", "?"))
        dt = str(m.get("dataType", "?"))
        n = len(m.get("alarms") or [])
        # Short enough for an 89px cell at 1366x640 (23/09/2026: "Whole number
        # (Int4)" and "1 alarm - only on this edge" clipped). The type is
        # Ignition's own name, the one the drift check reads out.
        note = _plural(n, "alarm") if n else ""
        if other_names and name not in other_names:
            note = "only here"
        rows.append({"member": _display_member(name), "type": dt, "note": note})
    return rows


def _type_words(type_id, variant):
    if type_id == ENGINE_UDT_NAME + "_v2":
        return "Version 2 (%s)" % type_id
    return "%s (%s)" % (VARIANT_WORDS.get(variant or "", str(variant or "").replace("_", " ")), type_id or "?")


def page_udt():
    s3, _e = _edge_state(0)
    s4, _e = _edge_state(1)
    r3 = (s3 or {}).get("udt") or {}
    r4 = (s4 or {}).get("udt") or {}
    n3 = [str(m.get("name")) for m in (r3.get("members") or [])]
    n4 = [str(m.get("name")) for m in (r4.get("members") or [])]
    t3 = r3.get("typeId") or r3.get("name")
    t4 = r4.get("typeId") or r4.get("name")
    types = _engine_types()
    v2 = ENGINE_UDT_NAME + "_v2"

    def matches(type_id, names, index):
        inst = _engine_instance(_edge(index))
        return (bool(names) and set(types.get(type_id or "", [])) == set(names)
                and inst["tagType"] == "UdtInstance" and inst["typeId"] == type_id
                and set(inst["members"]) == set(names))

    if not (n3 or n4):
        headline, level = "Waiting for the edges to report their tag layouts (UDT).", "neutral"
    elif t3 != t4:
        headline, level = ("Edge 3 uses %s and Edge 4 uses %s. Different names, so the cloud holds a template "
                           "for each and neither edge overrides the other." % (t3, t4)), "good"
    elif set(n3) != set(n4) or r3.get("hash") != r4.get("hash"):
        eng = types.get(t3, [])
        if eng and set(eng) == set(n3) and set(eng) != set(n4):
            kept, lost = "Edge 3", "Edge 4"
        elif eng and set(eng) == set(n4) and set(eng) != set(n3):
            kept, lost = "Edge 4", "Edge 3"
        else:
            kept = lost = None
        if kept:
            headline = ("The edges now disagree under one name. The cloud kept %s's layout, which it received "
                        "first, and ignored %s's." % (kept, lost))
        else:
            headline = ("The edges now disagree under one name, on a detail such as an alarm limit. The cloud's "
                        "template still has the same tags as both.")
        level = "warn"
    elif not (matches(t3, n3, 0) and matches(t4, n4, 1)):
        if set(types.get(t3, [])) != set(n3):
            headline = ("Both edges have the same layout, but the cloud's %s template is different: it kept "
                        "the old one. Only a cloud that forgets it (Fix 2) takes the new one." % t3)
        else:
            headline = ("Both edges have the same layout, but the cloud's copy of a station is not bound to "
                        "it (a plain folder, or the wrong template). Press Reset.")
        level = "warn"
    elif t3 == v2 and ENGINE_UDT_NAME in types:
        headline, level = ("Both edges use %s. The cloud still holds the old %s template, unused: press "
                           "Retire v1." % (v2, ENGINE_UDT_NAME)), "good"
    else:
        headline, level = ("Both edges use the same tag layout (%s), and the cloud's template matches both."
                           % t3), "good"

    order = []
    for name in n3 + n4:
        if name not in order:
            order.append(name)
    for names in types.values():
        for name in names:
            if name not in order:
                order.append(name)
    if not types:
        cloud_sub, cloud_members = "No template yet", []
    else:
        cloud_sub = ", ".join("%s (%s)" % (k, _plural(len(v), "tag")) for k, v in sorted(types.items()))
        cloud_members = []
        for name in order:
            held = [k for k in sorted(types) if name in types[k]]
            if held:
                cloud_members.append({"member": _display_member(name), "type": "",
                                      "note": "all templates" if len(held) == len(types) and len(types) > 1
                                      else ", ".join(held)})

    def edge_col(raw, names, other, type_id):
        sub = "%s, %s" % (_type_words(type_id, raw.get("variant")), _plural(len(names), "tag")) if names else "Not read yet"
        rows = _member_rows(raw, other)
        return {"sub": sub, "members": rows, "height": _udt_height(rows)}

    return {
        "headline": headline, "banner": "sp-banner sp-banner--%s" % level,
        "e0": edge_col(r3, n3, n4, t3), "e1": edge_col(r4, n4, n3, t4),
        "cloud": {"sub": cloud_sub, "members": cloud_members, "height": _udt_height(cloud_members)},
        "drift": udt_drift(),
        "ready": not not_ready(),
    }


# --- 6 Notifications ------------------------------------------------------------------


def _cloud_log_rows(limit):
    entries = [e for e in (cloud_notify_log() or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: -_as_long(e.get("ts")))
    rows = []
    for e in entries[:limit]:
        state = str(e.get("state") or "")
        word = "became active" if state.startswith("Active") else ("cleared" if state else "changed")
        rows.append({
            "ts": _when(e.get("ts")), "tsms": _as_long(e.get("ts")), "who": _who(e.get("node")),
            "what": "%s %s" % (str(e.get("alarm") or "Pump Fault").replace(" (cloud)", ""), word),
        })
    return rows


def page_notify():
    log = _cloud_log_rows(20)
    spk0, raw0 = _route_lines(0)
    spk1, raw1 = _route_lines(1)
    return {"spk0": spk0, "spk1": spk1, "raw0": raw0, "raw1": raw1,
            "log": log, "logCount": len(log)}


# --- MQTT traffic drawer ---------------------------------------------------------------

def _event_plain(m):
    t = m.get("type", "")
    who = _who(m.get("node"))
    mm = {}
    for x in (m.get("metrics") or []):
        mm[str(x.get("name"))] = x.get("value")
    if t == "NBIRTH":
        return "%s started up and sent all its tags (birth)" % who
    if t == "DBIRTH":
        return "The pump station on %s sent all its tags (birth)" % who
    if t in ("NDEATH", "DDEATH"):
        return "%s went offline (death)" % who
    if t == "NCMD":
        if [k for k in mm if "Rebirth" in k]:
            return "Cloud asked %s to resend all its tags (rebirth)" % who
        return "Cloud sent %s a command" % who
    if t == "DCMD":
        if "ackUser" in mm or ("id" in mm and "state" in mm):
            return "Cloud acknowledged an alarm on %s" % who
        for k, v in mm.items():
            if k.endswith("LevelSetpoint"):
                try:
                    return "Cloud set the level setpoint on %s to %.1f%%" % (who, float(v))
                except (Exception, JThrowable):
                    return "Cloud set the level setpoint on %s" % who
            if k.endswith("Mode"):
                return "Cloud switched %s to %s" % (who, v)
            if k.endswith("CloudNotice"):
                return "Cloud sent a notice to %s" % who
        return "Cloud sent %s a command" % who
    if t == "STATE":
        online = "true" in str(mm.get("state", "")).lower()
        return "Cloud gateway (hub) says it is %s (state)" % ("online" if online else "offline")
    if t == "NOTIFY":
        parts = str(mm.get("displayPath", "")).split("/")
        alarm = str(mm.get("alarm") or (parts[-1] if parts and parts[-1] else "An alarm"))
        words = EVENT_WORDS.get(str(mm.get("event", "")).lower(), "changed")
        text = "%s at %s %s" % (alarm, who, words)
        if mm.get("priority"):
            text += DOT + "%s priority" % mm.get("priority")
        return text
    if t in ("NDATA", "DDATA"):
        return "%s sent alarm updates (data)" % who
    return "%s: %s" % (who, t)


def _event_rows(types=None, limit=40, collapse="run", ring="events"):
    """Decoded broker events, newest first. collapse="run" folds consecutive
    identical rows; "all" folds every repeat. A folded row says how many it holds
    in its message ("x2"), so the When column only ever holds an age."""
    now = _now_millis()
    out = []
    index = {}
    for m in reversed(_wire_store().get(ring) or []):
        if types and m.get("type") not in types:
            continue
        what = _event_plain(m)
        direction = {"edge -> cloud": "Edge to cloud", "cloud -> edge": "Cloud to edge"}.get(m.get("direction"), "Broker")
        key = (what, direction)
        if collapse == "all" and key in index:
            index[key]["n"] += 1
            continue
        if collapse == "run" and out and (out[-1]["base"], out[-1]["dir"]) == key:
            out[-1]["n"] += 1
            continue
        if len(out) >= limit:
            break
        row = {"when": _age_text(now - m["ts"]), "dir": direction, "base": what,
               "kind": KIND_WORDS.get(m.get("type", ""), m.get("type", "")),
               "topic": str(m.get("topic", "")), "n": 1}
        out.append(row)
        index[key] = row
    for r in out:
        r["what"] = (u"%s (%s%d)" % (r["base"], unichr(0xd7), r["n"])) if r["n"] > 1 else r["base"]
    return out


def page_traffic():
    counts = _wire_store().get("counts", {})

    def c(*types):
        return "%d" % sum([counts.get(t, 0) for t in types])

    w = wire_summary()
    # Five tiles. Deaths, cloud STATE and the plain-MQTT count were three more
    # numbers nobody watching the demo had asked for; every one of them is
    # still a row in the list below, where it has words instead of a count.
    return {
        "rate": "%s msg/s" % w["rate"],
        "stats": {"data": c("NDATA", "DDATA"), "cmd": c("NCMD", "DCMD"),
                  "life": c("NBIRTH", "DBIRTH", "NDEATH", "DDEATH"),
                  "rate0": "%.1f msg/s" % _node_rate(0), "rate1": "%.1f msg/s" % _node_rate(1)},
        "events": _event_rows(None, 60, collapse="run"),
    }


# --- 5 UDT experiment: the two fixes (SPARKPLUG.md T9, T10) --------------------------
#
# Fix 1 (versioned): the changed layout is a NEW name, PumpStation_v2, and each
# edge's instance is moved to it one edge at a time. Fix 2 (scripted): the one
# changed definition goes to every edge, Engine forgets its copy, every edge
# re-births. Both re-birth with scope "module": `Refresh Edge Node` re-sends
# Transmission's CACHED Template, not the changed one (T8).

import time as _time

UDT_V2_NAME = ENGINE_UDT_NAME + "_v2"
ROLLOUT_VARIANT = "add_member"
ROLLOUT_WAIT_MS = 25000
CLOUD_REFERENCE_PATH = "[default]" + CLOUD_FOLDER + "/%s/PumpFault"
SETTLE_MS = 3000


def _engine_types():
    """name -> sorted member names, for every PumpStation* type Engine holds."""
    out = {}
    root = "%s%s" % (ENGINE_PROVIDER, ENGINE_TYPES_ROOT)
    try:
        results = system.tag.browse(root).getResults()
    except (Exception, JThrowable):
        return out
    for r in results or []:
        name = str(r["name"])
        if not name.startswith(ENGINE_UDT_NAME):
            continue
        try:
            out[name] = sorted(str(m["name"]) for m in system.tag.browse(str(r["fullPath"])).getResults())
        except (Exception, JThrowable):
            out[name] = []
    return out


def _engine_type_member_types(name):
    """member -> dataType for one PumpStation* type Engine holds; {} if Engine
    has no such type. A browse result carries the datatype, which
    _engine_types() (names only) does not -- and a datatype change is the
    divergence Engine keeps the OLD type for while the instance takes the new
    one (SPARKPLUG.md, Three field questions, B)."""
    out = {}
    if not name:
        return out
    try:
        results = system.tag.browse("%s%s/%s" % (ENGINE_PROVIDER, ENGINE_TYPES_ROOT, name)).getResults()
    except (Exception, JThrowable):
        return out
    for r in results or []:
        try:
            out[str(r["name"])] = str(r["dataType"])
        except (Exception, JThrowable):
            continue
    return out


def _drift_reason(members, type_members, instance, type_held):
    """The FIRST real difference between what one edge publishes and what the
    cloud holds, in plain words -- or "" when there is none. `members` is the
    edge's own (name, dataType) list, in the order the edge publishes them, so
    the difference reported is the first one a reader would find themselves.

    An alarm limit is not compared and cannot be: a Sparkplug Template carries
    no alarm configuration, so the cloud never receives one to disagree with
    (T8). That is the one divergence this check cannot see.
    """
    if not type_held:
        return "no template in the cloud"
    names = [n for n, _dt in members]
    for name, dt in members:
        if name not in type_members:
            return "%s missing in the cloud's template" % _display_member(name)
    for name in sorted(type_members):
        if name not in names:
            return "%s only in the cloud's template" % _display_member(name)
    for name, dt in members:
        if type_members[name] != dt:
            return "%s %s at the edge, %s in the template" % (_display_member(name), dt, type_members[name])
    for name, dt in members:
        if name not in instance:
            return "%s missing in the cloud's station" % _display_member(name)
    return ""


def udt_drift():
    """One row per edge: does what this edge PUBLISHES still match what the
    cloud holds? Read-only, and the one check that would have caught the silent
    divergence measured in SPARKPLUG.md, *Three field questions*, B.

    Three things are compared, all of them already read elsewhere on this page:
    the edge's own definition (its observer), the type Engine learnt, and that
    edge's Engine instance. An edge that is not publishing gets said so even
    when the shapes agree -- that is exactly the case the rollout reported as
    success with a 0.0 s pause.
    """
    stopped = not_ready()
    types = _engine_types()
    rows = []
    for e in EDGES:
        i = e["index"]
        row = {"edge": _short_name(e), "type": DASH, "tags": 0, "drift": DASH, "matches": None}
        if stopped:
            rows.append(row)
            continue
        state, err = _edge_state(i)
        udt = (state or {}).get("udt") or {}
        members = [(str(m.get("name")), str(m.get("dataType") or "?")) for m in (udt.get("members") or [])]
        type_id = udt.get("typeId") or udt.get("name")
        if not members:
            row["drift"] = "edge not answering"
            row["matches"] = False
            rows.append(row)
            continue
        row["type"] = str(type_id or "?")
        row["tags"] = len(members)
        reason = _drift_reason(members, _engine_type_member_types(type_id),
                               set(_engine_instance_members(e)), type_id in types)
        if not reason and not _edge_live(i):
            reason = "matches, but this edge is not publishing"
        row["drift"] = reason or "matches"
        row["matches"] = not reason
        rows.append(row)
    return rows


def retired_node_alarms():
    """Alarm rows the hub still holds for an edge node that is NOT one of this
    demo's -- "<group>/<node>" -> {rows, active, enginePath, engineTags}. A
    retired node never births again, so nothing ever clears its rows
    (SPARKPLUG.md, "Decommissioning an edge node"). De-duplicated on the event
    id, because queryStatus returns each Engine event twice (T-D4).
    `engineTags` says whether Engine still has the node's folder -- the rows
    can outlive it."""
    ours = set(e["agent"] for e in EDGES)
    seen, out = {}, {}

    def part(src, key):
        marker = ":/%s:" % key
        return src.split(marker, 1)[1].split(":", 1)[0] if marker in src else ""
    for r in _all_cloud_alarms():
        src = str(r.get("source", ""))
        node = part(src, "edge_node_id")
        if not node or node in ours:
            continue
        key = "%s/%s" % (part(src, "group_id"), node)
        ids = seen.setdefault(key, set())
        if r.get("id") in ids:
            continue
        ids.add(r.get("id"))
        row = out.setdefault(key, {"rows": 0, "active": 0, "source": src})
        row["rows"] = len(ids)
        if r.get("active"):
            row["active"] += 1
    for key, row in out.items():
        row["enginePath"] = "%s%s/%s" % (ENGINE_PROVIDER, ENGINE_NODES_ROOT, key)
        try:
            row["engineTags"] = bool(system.tag.exists(row["enginePath"]))
        except (Exception, JThrowable):
            row["engineTags"] = None
    return out


def _engine_instance(edge):
    """What Engine holds for one edge's station: tag type, typeId, members, Heartbeat."""
    path = _engine_site_folder(edge)
    info = {"path": path, "tagType": None, "typeId": None, "members": [], "heartbeat": None}
    try:
        if system.tag.exists(path):
            cfg = system.tag.getConfiguration(path, False)
            if cfg:
                info["tagType"] = str(cfg[0].get("tagType"))
                type_id = cfg[0].get("typeId")
                info["typeId"] = str(type_id) if type_id else None
    except (Exception, JThrowable), e:
        info["error"] = str(e)
    info["members"] = sorted(_engine_instance_members(edge))
    try:
        qvs = system.tag.readBlocking(["%s/%s" % (path, m) for m in info["members"]])
        info["memberQuality"] = dict((m, "%s=%s" % (qv.quality, qv.value))
                                     for m, qv in zip(info["members"], qvs))
    except (Exception, JThrowable):
        pass
    try:
        # A hub tag that references a member by its Engine PATH (C3) -- the
        # type name is not in that path, which is what keeps it working.
        ref = system.tag.readBlocking([CLOUD_REFERENCE_PATH % edge["agent"]])[0]
        info["reference"] = {"path": CLOUD_REFERENCE_PATH % edge["agent"], "quality": str(ref.quality)}
    except (Exception, JThrowable):
        pass
    try:
        qv = system.tag.readBlocking([_engine_tag_path(edge, "Heartbeat")])[0]
        info["heartbeat"] = {"value": None if qv.value is None else int(qv.value),
                             "quality": str(qv.quality),
                             "ts": qv.timestamp.getTime() if qv.timestamp else None}
    except (Exception, JThrowable):
        pass
    return info


def _edge_udt_fresh(edge_index):
    _STATE_CACHE.pop(edge_index, None)
    state, err = _edge_state(edge_index)
    udt = (state or {}).get("udt") or {}
    return {"typeId": udt.get("typeId") or udt.get("name"), "variant": udt.get("variant"),
            "definitions": udt.get("definitions") or [],
            "members": sorted(str(m.get("name")) for m in (udt.get("members") or [])),
            "error": err}


def _wire_timeline(node, since_ms):
    """Births, deaths and the longest silence in this node's station data since `since_ms`.

    Data = DDATA or DBIRTH on the Pumps device (a DBIRTH carries every value).
    The gap is measured from the last data message BEFORE since_ms, so an
    interruption that starts with the action is counted in full.
    """
    rows = [m for m in (_wire_store().get("messages") or []) if m.get("node") == node]
    data = [m["ts"] for m in rows if m.get("type") in ("DDATA", "DBIRTH")]
    before = [t for t in data if t < since_ms]
    after = [t for t in data if t >= since_ms]
    series = ([before[-1]] if before else []) + after
    gap = 0
    for x, y in zip(series, series[1:]):
        gap = max(gap, y - x)
    kinds = {}
    for m in rows:
        if m["ts"] >= since_ms and m.get("type") in ("NBIRTH", "DBIRTH", "NDEATH", "DDEATH", "NCMD"):
            kinds.setdefault(m["type"], []).append(m["ts"] - since_ms)
    births = [m for m in rows if m["ts"] >= since_ms and m.get("type") == "NBIRTH"]
    published = {}
    if births:
        for mm in births[-1].get("metrics") or []:
            name = str(mm.get("name", ""))
            if ENGINE_UDT_NAME in name and "/" in name:
                head, tail = name.split("/", 1)
                published.setdefault(head, []).append(tail)
    # dataMessages, not just the gap: a node that sent NOTHING while an action
    # ran has a longest gap of 0.0 s, which read as "no interruption" and was
    # the false success measured in Three field questions, B.
    return {"events": kinds, "maxGapMs": gap, "dataMessages": len(after),
            "lastDataAgoMs": (_now_millis() - data[-1]) if data else None,
            "definitionsInLastNbirth": dict((k, len(v)) for k, v in published.items())}


def _wait_for_births(indexes, since_ms, timeout_ms=ROLLOUT_WAIT_MS):
    nodes = set(_edge(i)["agent"] for i in indexes)
    deadline = since_ms + timeout_ms
    while _now_millis() < deadline:
        seen = set(m.get("node") for m in (_wire_store().get("messages") or [])
                   if m.get("type") == "DBIRTH" and m["ts"] >= since_ms)
        if nodes <= seen:
            _time.sleep(SETTLE_MS / 1000.0)
            return True
        _time.sleep(0.25)
    return False


def _engine_delete(paths):
    """Delete Engine tags that exist. Only ever under [MQTT Engine]."""
    doomed = [p for p in paths if str(p).startswith(ENGINE_PROVIDER) and system.tag.exists(p)]
    if doomed:
        system.tag.deleteTags(doomed)
    return doomed


def _instance_broken(index):
    inst = _engine_instance(_edge(index))
    own = _edge_udt_fresh(index)
    return (inst["tagType"] == "Folder" or not inst["members"]
            or (own["typeId"] and inst["typeId"] != own["typeId"]))


def _repair_instances(indexes):
    """Delete, on its own, any Engine instance that did not re-bind to its edge's type.

    Two ways it happens (T10): instances deleted just before a birth come back as
    plain Folders; and an instance whose type was deleted while it was bound, with
    the edge re-birthing under a DIFFERENT type name, stays bound to the deleted
    type with no members and dead values. A re-birth fixes neither. With the
    edge's type held, Engine rebuilds a UdtInstance from the next DDATA in < 5 s.
    """
    broken = [i for i in indexes if _instance_broken(i)]
    if broken:
        _engine_delete([_engine_site_folder(_edge(i)) for i in broken])
        deadline = _now_millis() + ROLLOUT_WAIT_MS
        while _now_millis() < deadline:
            if not any(_instance_broken(i) for i in broken):
                break
            _time.sleep(0.5)
    return [_edge(i)["agent"] for i in broken]


def _live(edge, wait_s=2.5):
    """Is Engine's copy of this edge's Heartbeat Good AND moving?"""
    a = _engine_instance(edge)["heartbeat"] or {}
    _time.sleep(wait_s)
    b = _engine_instance(edge)["heartbeat"] or {}
    return bool(str(b.get("quality", "")).startswith("Good") and a.get("value") != b.get("value"))


def udt_snapshot(since_ms=None):
    """Everything the two fixes are judged on, for every edge. Read-only."""
    now = _now_millis()
    since = long(since_ms) if since_ms else now - 60000
    edges = []
    for e in EDGES:
        edges.append({"node": e["agent"], "engine": _engine_instance(e), "edge": _edge_udt_fresh(e["index"]),
                      "wire": _wire_timeline(e["agent"], since)})
    # `drift` and `retiredNodes` ride the same open GET: both are read-only, and
    # `make mqtt-udt-check` is nothing but this snapshot printed one line per
    # edge, so there is one implementation of the comparison and no second
    # copy in bash to disagree with the page.
    return {"now": now, "since": since, "engineTypes": _engine_types(), "edges": edges,
            "drift": udt_drift(), "retiredNodes": retired_node_alarms()}


def _report(indexes, since_ms):
    """Plain words plus the numbers, for the edges an action touched."""
    types = _engine_types()
    facts, detail = [], {"engineTypes": types, "edges": {}}
    for i in indexes:
        e = _edge(i)
        inst = _engine_instance(e)
        wire = _wire_timeline(e["agent"], since_ms)
        own = _edge_udt_fresh(i)
        live = _live(e)
        type_members = types.get(inst["typeId"] or "", [])
        matches = bool(own["members"]) and set(type_members) == set(own["members"])
        detail["edges"][e["agent"]] = {"engine": inst, "wire": wire, "edge": own, "live": live,
                                       "engineTypeMatchesEdge": matches}
        # An edge that sent nothing has no pause to report: saying "paused
        # 0.0 s" for it is the false success of Three field questions, B.
        paused = ("sent NO messages while this ran" if not wire.get("dataMessages")
                  else "data paused %.1f s" % (wire["maxGapMs"] / 1000.0))
        facts.append("%s: edge uses %s (%d tags); cloud copy is %s of %s with %d tags, %s; values %s; %s" % (
                         _short_name(e), own["typeId"], len(own["members"]), inst["tagType"],
                         inst["typeId"], len(inst["members"]),
                         "matches the edge" if matches else "DOES NOT match the edge",
                         "live" if live else "NOT moving", paused))
    held = " and ".join("%s (%d tags)" % (k, len(v)) for k, v in sorted(types.items())) or "no template"
    return "Engine holds %s. %s." % (held, ". ".join(facts)), detail


def _all_ok(detail):
    return all(d["live"] and d["engineTypeMatchesEdge"] for d in detail["edges"].values())


def _rollout_v2(edge_index):
    edge = _edge(edge_index)
    if edge is None:
        return "No such edge.", {}
    t0 = _now_millis()
    result = _observer_action(edge_index, "udt_version", {"version": 2, "rebirth": True, "scope": "module"})
    if not result.get("ok"):
        return "Could not move %s to %s: %s" % (_short_name(edge), UDT_V2_NAME, result.get("message")), {}
    if not _wait_for_births([edge_index], t0):
        return "%s moved to %s, but no fresh birth arrived within %d s." % (
            _short_name(edge), UDT_V2_NAME, ROLLOUT_WAIT_MS / 1000), {}
    repaired = _repair_instances([edge_index])
    text, detail = _report([edge_index], t0)
    detail["repairedFolders"] = repaired
    return "%s now uses %s. %s" % (_short_name(edge), UDT_V2_NAME, text), detail


def udt_rollout_v2(edge_index):
    """Fix 1, one edge: move its instance to PumpStation_v2 and re-birth it."""
    return _rollout_v2(int(edge_index))[0]


def _retire_v1(engine_cleanup=True):
    on = [_edge_udt_fresh(e["index"])["typeId"] for e in EDGES]
    if any(t != UDT_V2_NAME for t in on):
        waiting = [_short_name(e) for e, t in zip(EDGES, on) if t != UDT_V2_NAME]
        return ("Not yet: %s still use%s %s. Move every edge to version 2 first." % (
            " and ".join(waiting), "s" if len(waiting) == 1 else "", ENGINE_UDT_NAME)), {}
    t0 = _now_millis()
    notes = []
    for e in EDGES:
        r = _observer_action(e["index"], "udt_retire", {"name": ENGINE_UDT_NAME, "rebirth": True, "scope": "module"})
        if not r.get("ok"):
            return "Could not retire %s on %s: %s" % (ENGINE_UDT_NAME, _short_name(e), r.get("message")), {}
    _wait_for_births([e["index"] for e in EDGES], t0)
    _repair_instances([e["index"] for e in EDGES])
    lingered = ENGINE_UDT_NAME in _engine_types()
    if lingered and engine_cleanup:
        used = [e for e in EDGES if _engine_instance(e)["typeId"] == ENGINE_UDT_NAME]
        if not used:
            _engine_delete(["%s%s/%s" % (ENGINE_PROVIDER, ENGINE_TYPES_ROOT, ENGINE_UDT_NAME)])
            notes.append("Engine still held the unused %s after both births; deleted it" % ENGINE_UDT_NAME)
    elif lingered:
        notes.append("Engine still holds the unused %s" % ENGINE_UDT_NAME)
    text, detail = _report([e["index"] for e in EDGES], t0)
    detail["v1LingeredOnEngine"] = lingered
    return "%s retired on both edges. %s%s" % (ENGINE_UDT_NAME, "; ".join(notes) + ". " if notes else "", text), detail


def udt_retire_v1():
    """Fix 1, the end: delete PumpStation at every edge once none uses it, then on Engine."""
    return _retire_v1(True)[0]


def _rollout_all(variant=ROLLOUT_VARIANT, delete_engine_type=True, delete_engine_instances=False,
                 settle_ms=0):
    # PRE-CHECK, and it is not politeness. Fix 2 keeps ONE type name, so it is
    # correct only for a fleet reachable all at once: an edge that misses the
    # push births its old layout on return, collides, and loses the change in
    # the cloud while still publishing it (T8's first-birth-wins, measured
    # again in Three field questions, B). Worse, the push travels to the
    # observer over the container network, so a cut edge TAKES the definition
    # and the run then reports "matches the edge" for a node that sent nothing.
    # _edge_live() is both halves of "not publishing": Engine's Node
    # Info/Online, and the wire witness having heard from it.
    quiet = [_short_name(e) for e in EDGES if not _edge_live(e["index"])]
    if quiet:
        return ("Refused: %s not publishing. Fix 2 keeps one type name, so every edge must take the "
                "new definition in the same window -- one that misses it collides on return and loses "
                "the change in the cloud. Use Fix 1 (a new name) instead, or wait for the link."
                % " and ".join(quiet),
                {"refused": True, "notPublishing": quiet})
    # Instances are left alone: Engine 5.0.4 re-binds them to the re-learnt type
    # (T10), and deleting them just before a birth came back as plain Folders.
    t0 = _now_millis()
    for e in EDGES:
        if variant == "base":
            r = _observer_action(e["index"], "udt_restore", {})
        else:
            r = _observer_action(e["index"], "udt_diverge", {"variant": variant})
        if not r.get("ok"):
            return "Stopped: %s did not take the new definition: %s" % (_short_name(e), r.get("message")), {}
    deleted = []
    if delete_engine_instances:
        deleted += _engine_delete([_engine_site_folder(e) for e in EDGES])
    if delete_engine_type:
        deleted += _engine_delete(["%s%s/%s" % (ENGINE_PROVIDER, ENGINE_TYPES_ROOT, ENGINE_UDT_NAME)])
    if deleted and settle_ms:
        _time.sleep(settle_ms / 1000.0)
    t1 = _now_millis()
    for e in EDGES:
        _observer_action(e["index"], "rebirth", {"scope": "module"})
    _wait_for_births([e["index"] for e in EDGES], t1)
    repaired = _repair_instances([e["index"] for e in EDGES])
    text, detail = _report([e["index"] for e in EDGES], t0)
    detail["deleted"] = deleted
    detail["repairedFolders"] = repaired
    detail["pushToRebirthMs"] = t1 - t0
    detail["settleMs"] = settle_ms
    return text, detail


def udt_rollout_all():
    """Fix 2: the one changed definition to every edge, Engine forgets its copy, every edge re-births."""
    text, detail = _rollout_all()
    if not detail or detail.get("refused"):
        return text
    verdict = ("Both edges and the cloud agree on the new layout." if _all_ok(detail) else
               "The edges and the cloud do NOT all agree -- see below.")
    return "Rolled out to every edge. %s %s" % (verdict, text)


def udt_reset_baseline():
    """Both edges back on the original PumpStation, v2 gone everywhere, Engine re-learnt from scratch."""
    t0 = _now_millis()
    for e in EDGES:
        for action, body in (("udt_version", {"version": 1}), ("udt_restore", {}),
                             ("udt_retire", {"name": UDT_V2_NAME})):
            r = _observer_action(e["index"], action, body)
            if not r.get("ok"):
                return "Reset stopped at %s (%s): %s" % (_short_name(e), action, r.get("message"))
    _engine_delete(["%s%s/%s" % (ENGINE_PROVIDER, ENGINE_TYPES_ROOT, n) for n in (ENGINE_UDT_NAME, UDT_V2_NAME)])
    t1 = _now_millis()
    for e in EDGES:
        _observer_action(e["index"], "rebirth", {"scope": "module"})
    _wait_for_births([e["index"] for e in EDGES], t1)
    _repair_instances([e["index"] for e in EDGES])
    text, detail = _report([e["index"] for e in EDGES], t0)
    return "%s %s" % ("Back to the start: both edges on the original layout." if _all_ok(detail)
                      else "Reset ran, but something does not agree yet.", text)


# --- the UDT route (sparkplug/udt): GET is open and read-only, POST needs the token --

_UDT_ROUTE = {
    "rollout_v2": lambda a: _rollout_v2(int(a.get("edge_index", 1))),
    "retire_v1": lambda a: _retire_v1(bool(a.get("engine_cleanup", True))),
    "rollout_all": lambda a: _rollout_all(a.get("variant", ROLLOUT_VARIANT),
                                          bool(a.get("delete_engine_type", True)),
                                          bool(a.get("delete_engine_instances", False)),
                                          int(a.get("settle_ms", 0))),
    "reset_baseline": lambda a: (udt_reset_baseline(), {}),
    "edge_action": lambda a: (_observer_action(int(a["edge_index"]), str(a["action"]), a.get("body") or {}), {}),
    "engine_delete": lambda a: ("deleted", {"deleted": _engine_delete(a.get("paths") or [])}),
}


def handle_udt_get(request):
    try:
        params = request.get("params") or {}
        return {"json": udt_snapshot(params.get("since"))}
    except (Exception, JThrowable), e:
        return {"json": {"ok": False, "message": str(e)}}


def handle_udt_post(request):
    from java.lang import String as JString
    from java.security import MessageDigest
    response = request.get("servletResponse")
    try:
        presented = request["servletRequest"].getHeader("X-WD-Token")
    except (Exception, JThrowable):
        presented = None
    expected = _wd_token()
    if not (presented and expected and MessageDigest.isEqual(
            JString(expected).getBytes("UTF-8"), JString(unicode(presented)).getBytes("UTF-8"))):
        try:
            response.setStatus(403)
        except (Exception, JThrowable):
            pass
        return {"json": {"ok": False, "message": "refused: missing or wrong X-WD-Token"}}
    try:
        body = request.get("postData")
        if isinstance(body, basestring):
            body = system.util.jsonDecode(body)
        body = body or {}
        fn = _UDT_ROUTE.get(str(body.get("fn")))
        if fn is None:
            return {"json": {"ok": False, "message": "fn must be one of %s" % ", ".join(sorted(_UDT_ROUTE))}}
        t0 = _now_millis()
        message, detail = fn(body.get("args") or {})
        return {"json": {"ok": True, "message": message, "detail": detail, "tookMs": _now_millis() - t0}}
    except (Exception, JThrowable), e:
        _logger().warn("udt route failed: %s" % _because(e))
        return {"json": {"ok": False, "message": _because(e)}}
