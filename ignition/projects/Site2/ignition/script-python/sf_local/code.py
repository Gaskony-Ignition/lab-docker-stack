"""The Store & Forward demo's audit and alarm controls, on the edge itself.

Two WebDev routes call this, so the hub's console works the same for both edges
without the Gateway Network: Edge 2 is the edge that must never need it.

  POST /system/webdev/Edge/sf/action  {"kind": "audit"|"alarm"|"clear"}
       X-WD-Token required (the hub's sf-token, scripts/sf-audit-alarms.sh)
  GET  /system/webdev/Edge/sf/local?since=<epoch ms>
       open and read-only: this edge's own audit and alarm journal records, the
       "local" half of the evidence table

Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import Throwable as JThrowable

LOG = "SFEvidence"
TOKEN_PROVIDER = "wd"
TOKEN_NAME = "sf-token"
AUDIT_PROFILE = "EdgeAuditProfile"
JOURNAL = "EdgeJournal"
FAULT = "[edge]MQTT Tags/Demo/Fault"
MAX_ROWS = 200


def _logger():
    return system.util.getLogger(LOG)


def token_ok(presented):
    """Constant-time check of an X-WD-Token against the secret store."""
    if not presented:
        return False
    try:
        with system.secrets.readSecretValue(TOKEN_PROVIDER, TOKEN_NAME) as plain:
            expected = unicode(plain.getSecretAsString())
    except (Exception, JThrowable), e:
        _logger().warn("action refused: cannot read %s/%s (%s)" % (TOKEN_PROVIDER, TOKEN_NAME, e))
        return False
    if not expected:
        return False
    from java.lang import String as JString
    from java.security import MessageDigest
    return bool(MessageDigest.isEqual(JString(expected).getBytes("UTF-8"),
                                      JString(unicode(presented)).getBytes("UTF-8")))


def act(body):
    kind = str((body or {}).get("kind") or "")
    if kind == "audit":
        system.util.audit(action="operator note", actionTarget="Store & Forward demo",
                          actionValue=str((body or {}).get("note") or "recorded from the console"),
                          auditProfile=AUDIT_PROFILE, actor="demo operator")
        return {"ok": True, "message": "audit record written on this edge"}
    if kind in ("alarm", "clear"):
        q = system.tag.writeBlocking([FAULT], [kind == "alarm"])[0]
        if not q.isGood():
            return {"ok": False, "message": "could not write the fault tag: %s" % q}
        return {"ok": True, "message": "fault raised" if kind == "alarm" else "fault cleared"}
    return {"ok": False, "message": "kind is audit, alarm or clear"}


def _rows(ds, limit):
    cols = [str(c) for c in ds.getColumnNames()]
    out = []
    # The NEWEST rows: both queries return oldest first.
    for r in range(max(0, ds.getRowCount() - limit), ds.getRowCount()):
        row = {}
        for i, c in enumerate(cols):
            v = ds.getValueAt(r, i)
            if hasattr(v, "getTime"):
                v = long(v.getTime())
            elif v is not None and not isinstance(v, (int, long, float, bool)):
                v = unicode(v)
            row[c] = v
        out.append(row)
    return out


def local(since_ms):
    """This edge's own audit and journal records since `since_ms`. Never raises."""
    end = system.date.now()
    start = system.date.fromMillis(long(since_ms))
    out = {"ok": True, "audit": [], "alarms": [], "errors": []}
    try:
        out["audit"] = _rows(system.util.queryAuditLog(
            auditProfileName=AUDIT_PROFILE, startDate=start, endDate=end), MAX_ROWS)
    except (Exception, JThrowable), e:
        out["errors"].append("audit: %s" % e)
    try:
        out["alarms"] = _rows(system.alarm.queryJournal(
            startDate=start, endDate=end, journalName=JOURNAL).getDataset(), MAX_ROWS)
    except (Exception, JThrowable), e:
        out["errors"].append("alarms: %s" % e)
    return out
