"""The cloud's half of the notification demo: a HUB pipeline that answers the edge.

The hub project SparkplugCloud has one alarm pipeline, CloudNotify, whose one
Script block calls notify() here. What feeds it are HUB alarms -- reference
tags at [default]SparkplugDemo/<node>/PumpFault on the Engine tags, which
scripts/sparkplug-setup.sh creates -- because an alarm that arrives over
Sparkplug never enters a hub pipeline. Engine's propagated alarm events carry
the edge's pipeline name, and a hub pipeline with exactly that qualified name
is never evaluated (measured 11/09/2026).

The answer goes back the only way the cloud can reach an edge over MQTT:
notify() writes the Engine tag CloudNotice, Engine sends it as a DCMD, and
Transmission writes the edge's own CloudNotice tag, which the edge's operator
page shows. Transmission subscribes to nothing but Sparkplug commands, so a
raw MQTT topic could not do this.

Jython 2.7 -- `except X, e`, not `as e`.
"""
import json
import re
import threading

from java.lang import System as JSystem
from java.lang import Throwable as JThrowable

GROUP = "AlarmDemo"
DEVICE = "Pumps"
CLOUD_TAG = "CloudNotice"
FOLDER = "[default]SparkplugDemo"
LOG_PATH = FOLDER + "/CloudNotifyLog"
LOG_MAX = 20
LOG = "SparkplugCloud"

_LOCK = threading.Lock()


def logger():
    return system.util.getLogger(LOG)


def node_of(source):
    """Which edge an alarm is about.

    A reference-tag alarm: prov:default:/tag:SparkplugDemo/Edge3/PumpFault:/alm:...
    An alarm configured on the Engine tag itself: .../edge_node_id:Edge3:/...
    """
    match = re.search(r"/tag:SparkplugDemo/([^/:]+)/", source) or \
        re.search(r"edge_node_id:([^:/]+)", source)
    return match.group(1) if match else None


def cloud_path(node):
    return "[MQTT Engine]Edge Nodes/%s/%s/%s/%s" % (GROUP, node, DEVICE, CLOUD_TAG)


def recent():
    try:
        qv = system.tag.readBlocking([LOG_PATH])[0]
        rows = json.loads(qv.value or "[]") if qv.quality.isGood() else []
        return rows if isinstance(rows, list) else []
    except (Exception, JThrowable):
        return []


def _record(entry):
    _LOCK.acquire()
    try:
        system.tag.writeBlocking([LOG_PATH], [json.dumps(([entry] + recent())[:LOG_MAX])])
    finally:
        _LOCK.release()


def notify(event, pipeline):
    """Called by CloudNotify's Script block. Never raises."""
    now = JSystem.currentTimeMillis()
    entry = {"ts": now, "pipeline": pipeline}
    try:
        entry["source"] = unicode(event.getSource())
        entry["alarm"] = unicode(event.getName())
        entry["state"] = unicode(event.getState())
        entry["priority"] = unicode(event.getPriority())
    except (Exception, JThrowable), e:
        entry["error"] = unicode(e)[:200]
    node = node_of(entry.get("source") or "")
    entry["node"] = node
    if node:
        text = u"CLOUD %s: %s is %s -- seen by the hub's %s pipeline" % (
            system.date.format(system.date.fromMillis(now), "HH:mm:ss"),
            entry.get("alarm"), entry.get("state"), pipeline)
        try:
            quality = system.tag.writeBlocking([cloud_path(node)], [text])[0]
            entry["write"] = {"tag": cloud_path(node), "ok": bool(quality.isGood()),
                              "quality": unicode(quality)}
        except (Exception, JThrowable), e:
            entry["write"] = {"tag": cloud_path(node), "ok": False, "error": unicode(e)[:200]}
    try:
        _record(entry)
    except (Exception, JThrowable), e:
        logger().warn("CloudNotifyLog write failed: %s" % e)
    logger().info("%s %s for %s: CloudNotice %s" % (
        entry.get("alarm"), entry.get("state"), node,
        (entry.get("write") or {}).get("ok")))
    return entry
