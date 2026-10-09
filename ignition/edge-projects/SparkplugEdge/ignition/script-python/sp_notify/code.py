"""Alarm notifications across an MQTT-only architecture -- the demo's second question.

The question asked: "what can we do with regards to passing alarm pipeline
notifications via MQTT". An Edge gateway runs alarm notification pipelines
like any other; what it cannot do is hand the notification to the cloud,
because nothing but the broker connects them. So the edge's pipeline Script
block calls notify() here, which sends the SAME document two ways, so the
demo can compare them side by side and under a broker cut:

  1. RAW MQTT -- system.cirruslink.transmission.publish to notify/<group>/<node>,
     plain JSON, which the hub's MQTT Engine turns into tags through a custom
     namespace ([MQTT Engine]Notify/...). Not Sparkplug: no birth, no sequence,
     no historical flag. The RPC client queues it in memory while disconnected
     (all 7 of a 45 s cut arrived, in order), and it needs its OWN CA and
     credentials -- the rpc* fields on the Transmission server (ign-mqtt.sh).
  2. SPARKPLUG -- the document written to a String memory tag, EdgeNotice,
     beside the station in the transmitter's folder, so it crosses as an
     ordinary DDATA metric: sequenced, and buffered by store-and-forward.

Every attempt is also kept in a small bounded log (LOG_MAX entries) that the
observer serves as `notifications`. The log tag lives OUTSIDE the transmitter's
folder so the log itself is never published.

Jython 2.7 -- `except X, e`, not `as e`.
"""
import json
import threading

from java.lang import String as JString
from java.lang import System as JSystem
from java.lang import Throwable as JThrowable

import sp_site
import sp_tx

LOG_FOLDER = "SparkplugDemo"   # [edge]SparkplugDemo -- never published
LOG_TAG = "NotifyLog"
LOG_MAX = 20
NOTICE_TAG = "EdgeNotice"      # edge -> cloud, as a Sparkplug metric
CLOUD_TAG = "CloudNotice"      # cloud -> edge, written by the hub via DCMD
QOS = 1
REPLAY_MS = 60000              # a transition older than this is a replay, not news
BACKLOG_MAX = 3                # raw publishes allowed to wait; the rest are skipped


def topic():
    return "notify/%s/%s" % (sp_site.GROUP, sp_site.node_id())


def notice_path():
    return "%s/%s" % (sp_site.device_path(), NOTICE_TAG)


def cloud_path():
    return "%s/%s" % (sp_site.device_path(), CLOUD_TAG)


def log_path():
    return "[%s]%s/%s" % (sp_site.PROVIDER, LOG_FOLDER, LOG_TAG)


def ensure_tags():
    """EdgeNotice / CloudNotice beside the station, and the log folder. Only
    what is missing -- configure on an existing tag would re-birth for nothing."""
    made = []
    for path, name in ((notice_path(), NOTICE_TAG), (cloud_path(), CLOUD_TAG)):
        if not system.tag.exists(path):
            system.tag.configure(sp_site.device_path(),
                                 [{"name": name, "tagType": "AtomicTag", "valueSource": "memory",
                                   "dataType": "String", "value": ""}], "a")
            made.append(name)
    if not system.tag.exists(log_path()):
        system.tag.configure("[%s]" % sp_site.PROVIDER,
                             [{"name": LOG_FOLDER, "tagType": "Folder",
                               "tags": [{"name": LOG_TAG, "tagType": "AtomicTag",
                                         "valueSource": "memory", "dataType": "String",
                                         "value": "[]"}]}], "m")
        made.append(LOG_TAG)
    return made


def _get(event, *accessors):
    for accessor in accessors:
        try:
            value = accessor(event)
            if value is not None:
                return value
        except (Exception, JThrowable):
            pass
    return None


def _event_doc(event):
    """What a notification says about its alarm. Every accessor guarded on its
    own -- a pipeline event and a status-table event expose different ones."""
    if event is None:
        return {"alarm": "manual test", "priority": None, "state": None, "event": "test"}
    state = unicode(_get(event, lambda e: e.getState(), lambda e: e.get("state")) or "")
    active = _get(event, lambda e: e.getActiveData())
    # Which transition this is = the NEWEST of the three event-data stamps. The
    # state string cannot say: an ack after a clear and a clear after an ack both
    # read "Cleared, Acknowledged".
    stamps = []
    for kind_name, data in (("active", active),
                            ("clear", _get(event, lambda e: e.getClearedData())),
                            ("ack", _get(event, lambda e: e.getAckData()))):
        stamp = _get(data, lambda d: long(d.getTimestamp())) if data is not None else None
        if stamp:
            stamps.append((stamp, kind_name))
    if stamps:
        transition_time, kind = max(stamps)
    else:
        lowered = state.lower()
        transition_time, kind = None, ("clear" if lowered.startswith("clear") else "active")
    name = unicode(_get(event, lambda e: e.getName(), lambda e: e.get("name")) or "")
    source = unicode(_get(event, lambda e: e.getSource()) or "")
    display = unicode(_get(event, lambda e: e.getDisplayPath()) or "")
    if not display and "/tag:" in source:
        # An alarm with no display path configured reports "" here, and the
        # console would show a blank row: fall back to <tag path>/<alarm>.
        display = u"%s/%s" % (source.split("/tag:", 1)[1].split(":/alm:", 1)[0], name)
    return {
        "id": unicode(_get(event, lambda e: e.getId()) or ""),
        "alarm": name,
        "priority": unicode(_get(event, lambda e: e.getPriority(), lambda e: e.get("priority")) or ""),
        "state": state,
        "event": kind,
        "displayPath": display,
        "source": source,
        "eventTime": _get(active, lambda d: long(d.getTimestamp())) if active is not None else None,
        "transitionTime": transition_time,
    }


def _server():
    rows = [r for r in sp_tx.resources("server") if r["enabled"]]
    return rows[0]["name"] if rows else None


def publish_mqtt(doc):
    """Road 1: plain JSON on notify/<group>/<node>. Returns what happened."""
    server = _server()
    out = {"topic": topic(), "server": server, "ok": False}
    if server is None:
        out["error"] = "no enabled Transmission server"
        return out
    t0 = JSystem.currentTimeMillis()
    try:
        payload = JString(json.dumps(doc)).getBytes("UTF-8")
        system.cirruslink.transmission.publish(server, topic(), payload, QOS, False)
        out["ok"] = True
    except (Exception, JThrowable), e:
        out["error"] = unicode(e)[:300]
    out["ms"] = JSystem.currentTimeMillis() - t0
    return out


def write_sparkplug(doc):
    """Road 2: the same JSON as a Sparkplug metric (EdgeNotice)."""
    try:
        quality = system.tag.writeBlocking([notice_path()], [json.dumps(doc)])[0]
        return {"tag": notice_path(), "ok": bool(quality.isGood()), "quality": unicode(quality)}
    except (Exception, JThrowable), e:
        return {"tag": notice_path(), "ok": False, "error": unicode(e)[:300]}


def recent():
    """The bounded log, newest first. Never raises."""
    try:
        qv = system.tag.readBlocking([log_path()])[0]
        rows = json.loads(qv.value or "[]") if qv.quality.isGood() else []
        return rows if isinstance(rows, list) else []
    except (Exception, JThrowable):
        return []


# The log is read-modify-written by the pipeline thread AND by the raw-publish
# thread that patches its outcome in afterwards.
_LOCK = threading.Lock()


def _record(entry):
    _LOCK.acquire()
    try:
        rows = [entry] + recent()
        system.tag.writeBlocking([log_path()], [json.dumps(rows[:LOG_MAX])])
    finally:
        _LOCK.release()


def _update(nid, field, value):
    _LOCK.acquire()
    try:
        rows = recent()
        for row in rows:
            if row.get("nid") == nid:
                row[field] = value
                system.tag.writeBlocking([log_path()], [json.dumps(rows)])
                return True
        return False
    finally:
        _LOCK.release()


# One raw publish at a time. While the RPC client could not connect, two
# concurrent calls raised NullPointerException "this.rpcClientThread is null"
# inside it; healthy, five at once were fine (measured). Cheap insurance.
_PUB_LOCK = threading.Lock()
# How many are queued behind it. Each takes 1-3 s while the RPC client is down,
# so an alarm that flaps would otherwise stack up threads without limit.
_BACKLOG = [0]


def _queue_publish():
    _LOCK.acquire()
    try:
        if _BACKLOG[0] >= BACKLOG_MAX:
            return False
        _BACKLOG[0] += 1
        return True
    finally:
        _LOCK.release()


def _publish_done():
    _LOCK.acquire()
    try:
        _BACKLOG[0] = max(0, _BACKLOG[0] - 1)
    finally:
        _LOCK.release()


def _publish_later(nid, doc):
    _PUB_LOCK.acquire()
    try:
        result = publish_mqtt(doc)
    finally:
        _PUB_LOCK.release()
        _publish_done()
    try:
        _update(nid, "published", result)
    except (Exception, JThrowable), e:
        sp_site.logger().warn("notification log update failed: %s" % e)
    sp_site.logger().info("notification %s/%s raw MQTT: ok=%s in %s ms" % (
        doc.get("alarm"), doc.get("event"), result.get("ok"), result.get("ms")))


def notify(event, pipeline):
    """Called by the edge's alarm pipeline Script block.

    Returns the log entry. Never raises: a Script block that throws stops the
    pipeline, and the cloud would simply hear nothing.

    Sparkplug FIRST, and the raw publish on its own thread. While Transmission's
    RPC client is disconnected, publish() blocks 1-2 s per call before it
    returns (measured 11/09/2026); run inline, that delayed EdgeNotice and held
    the pipeline's next event behind it.
    """
    now = JSystem.currentTimeMillis()
    doc = {"edge": sp_site.system_name(), "group": sp_site.GROUP, "node": sp_site.node_id(),
           "site": sp_site.site_name(), "pipeline": pipeline, "ts": now}
    try:
        doc.update(_event_doc(event))
    except (Exception, JThrowable), e:
        doc["error"] = unicode(e)[:200]
    # When an alarm re-activates, the pipeline is handed the PREVIOUS instance
    # of it once more -- already cleared and acknowledged, its last transition
    # long past (measured: 97 min old, 6 ms after the new activation). Sent on,
    # it reads as a fresh "clear" racing the real "active".
    stamp = doc.get("transitionTime")
    if event is not None and stamp and now - stamp > REPLAY_MS:
        sp_site.logger().info("notification skipped: %s/%s is a replay of a transition %d s old" % (
            doc.get("alarm"), doc.get("event"), (now - stamp) // 1000))
        return {"skipped": "replay", "alarm": doc.get("alarm"), "event": doc.get("event"),
                "ageMs": now - stamp}
    nid = "%d-%s" % (now, doc.get("id") or "test")
    entry = dict(doc)
    entry["nid"] = nid
    entry["sparkplug"] = write_sparkplug(doc)
    queued = _queue_publish()
    entry["published"] = ({"topic": topic(), "pending": True} if queued else
                          {"topic": topic(), "ok": False,
                           "error": "skipped: %d raw publishes already waiting" % BACKLOG_MAX})
    try:
        _record(entry)
    except (Exception, JThrowable), e:
        sp_site.logger().warn("notification log write failed: %s" % e)
    if queued:
        try:
            system.util.invokeAsynchronous(lambda: _publish_later(nid, doc))
        except (Exception, JThrowable), e:
            _publish_done()
            entry["published"] = {"topic": topic(), "ok": False, "error": unicode(e)[:300]}
    sp_site.logger().info("notification %s/%s via %s: sparkplug ok=%s" % (
        doc.get("alarm"), doc.get("event"), pipeline, entry["sparkplug"].get("ok")))
    return entry
