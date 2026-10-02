"""The observer document: what this edge's own tags, alarms and UDT look like, as JSON.

Served by GET /system/webdev/Edge/sparkplug/state and read by the hub's console.
It is the OBSERVATION road and the page labels it so: it lets the audience see
the edge's side of a comparison, and it never carries process data anywhere.
Process data reaches the cloud only as Sparkplug B, which is the demo's claim.

So nothing here may write, and nothing here may carry a secret -- the route is
open to anyone who can reach the gateway.

Never raises. A polled endpoint that throws renders as a blank panel on the
console, and a blank panel reads as "the edge is down". Anything that could not
be read is listed in `errors` instead, next to the parts that could.

Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import System as JSystem
from java.lang import Throwable as JThrowable

import sp_site
import sp_tx
import sp_udt


def _safe(fn, errors=None, what=None):
    try:
        return fn()
    except (Exception, JThrowable), e:
        if errors is not None and what:
            errors.append("%s: %s" % (what, e))
        return None


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, long, float, basestring)):
        return value
    if hasattr(value, "getTime"):          # java.util.Date
        return value.getTime()
    return unicode(value)


def _millis(event_data):
    if event_data is None:
        return None
    return _safe(lambda: long(event_data.getTimestamp()))


def _ack_user(event):
    """Who acknowledged it. The property lives in the ack event data."""
    data = _safe(lambda: event.getAckData())
    if data is None:
        return None
    try:
        from com.inductiveautomation.ignition.common.alarming.config import CommonAlarmProperties
        user = data.get(CommonAlarmProperties.AckUser)
        if user is not None:
            return unicode(user)
    except (Exception, JThrowable):
        pass
    return None


def alarms(errors=None):
    """This station's alarms as the edge's own alarm status holds them.

    queryStatus's source filter needs `prov:<name>:/tag:*` -- `prov:<name>:*`
    matches nothing and returns an empty list with no error (toolkit), and an
    empty list is indistinguishable from "no alarms". So the filter is the broad
    working one and the station's folder is matched here.

    Every accessor is guarded on its own: a whole-row try/except turns one bad
    accessor into "no alarms while alarms are standing".
    """
    out = []
    try:
        events = system.alarm.queryStatus(source=["prov:%s:/tag:*" % sp_site.PROVIDER])
    except (Exception, JThrowable), e:
        if errors is not None:
            errors.append("queryStatus: %s" % e)
        return out
    notes = sp_udt.alarm_notes()
    marker = "/tag:%s/" % sp_site.FOLDER
    for event in events:
        source = _safe(lambda: unicode(event.getSource())) or u""
        if marker not in source:
            continue
        # getState() renders as "Active, Unacknowledged" / "Cleared, Acknowledged"
        # (measured 8.3.8), NOT the enum names ActiveUnacked/ClearAcked -- so an
        # endswith("Acked") test read every alarm as unacknowledged.
        state = _safe(lambda: unicode(event.getState())) or u""
        lowered = state.lower()
        name = _safe(lambda: unicode(event.getName()))
        out.append({
            "id": _safe(lambda: unicode(event.getId())),
            "source": source,
            "displayPath": _safe(lambda: unicode(event.getDisplayPath())),
            "name": name,
            "priority": _safe(lambda: unicode(event.getPriority())),
            "state": state,
            "active": lowered.startswith("active"),
            "acked": "unacknowledged" not in lowered and ("acknowledged" in lowered
                                                          or lowered.endswith("acked")),
            "activeTs": _millis(_safe(lambda: event.getActiveData())),
            "clearTs": _millis(_safe(lambda: event.getClearedData())),
            "ackTs": _millis(_safe(lambda: event.getAckData())),
            "ackedBy": _ack_user(event),
            "notes": notes.get(name),
        })
    out.sort(key=lambda a: a.get("activeTs") or 0, reverse=True)
    return out


def _tags(udt, errors):
    names = [m["name"] for m in udt.get("members", [])]
    if not names:
        return []
    alarmed = dict((m["name"], bool(m["alarms"])) for m in udt["members"])
    paths = [sp_site.member_path(n) for n in names]
    try:
        qvs = system.tag.readBlocking(paths)
    except (Exception, JThrowable), e:
        errors.append("readBlocking: %s" % e)
        return []
    rows = []
    for name, path, qv in zip(names, paths, qvs):
        rows.append({
            "path": "%s/%s" % (sp_site.site_name(), name),
            "name": name,
            "fullPath": path,
            "value": _jsonable(qv.value),
            "quality": unicode(qv.quality),
            "ts": _jsonable(qv.timestamp),
            "writable": name in sp_udt.WRITABLE,
            "alarmed": alarmed.get(name, False),
        })
    return rows


def _pipelines():
    """The alarm pipelines this edge's project runs -- what an alarm can name."""
    try:
        project = system.util.getProjectName()
    except (Exception, JThrowable):
        project = "Edge"
    return {"project": project,
            "pipelines": [unicode(p) for p in (system.alarm.listPipelines(project) or [])],
            "boundTo": sp_site.PIPELINE_REF}


def _notifications():
    import sp_notify
    return sp_notify.recent()


def _notices():
    """The two notice tags as the edge holds them: what it last sent the cloud
    (EdgeNotice) and what the cloud last sent it (CloudNotice, via DCMD)."""
    import sp_notify
    paths = [sp_notify.notice_path(), sp_notify.cloud_path()]
    qvs = system.tag.readBlocking(paths)
    out = {}
    for key, path, qv in zip(("edgeNotice", "cloudNotice"), paths, qvs):
        out[key] = {"path": path, "value": _jsonable(qv.value), "quality": unicode(qv.quality),
                    "ts": _jsonable(qv.timestamp)}
    return out


def state():
    errors = []
    udt = _safe(sp_udt.current, errors, "udt") or {"name": sp_site.UDT, "members": []}
    return {
        "ok": True,
        "gateway": sp_site.system_name(),
        "node": sp_site.node_id(),
        "group": sp_site.GROUP,
        "site": sp_site.site_name(),
        # The names scripts/sparkplug-setup.sh configures the transmitter from,
        # so the bash side never repeats them.
        "provider": sp_site.PROVIDER,
        "folder": sp_site.FOLDER,
        "journal": sp_site.JOURNAL,
        "device": sp_site.DEVICE,
        "tagRoot": sp_site.device_path(),
        "instancePath": sp_site.instance_path(),
        "now": JSystem.currentTimeMillis(),
        "transmission": _safe(sp_tx.status, errors, "transmission") or {},
        "tags": _tags(udt, errors),
        "alarms": alarms(errors),
        "udt": udt,
        # What the edge's notification pipeline sent, newest first, bounded
        # (sp_notify.LOG_MAX): each entry says which road it took and whether
        # it got through -- `published` (raw MQTT) and `sparkplug` (EdgeNotice).
        "notifications": _safe(_notifications, errors, "notifications") or [],
        "notices": _safe(_notices, errors, "notices") or {},
        "pipelines": _safe(_pipelines, errors, "pipelines") or {},
        "errors": errors,
    }
