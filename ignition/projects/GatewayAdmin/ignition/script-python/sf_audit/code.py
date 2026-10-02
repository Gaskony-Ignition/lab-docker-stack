"""Audit records and alarm events from the Store & Forward edges: the hub's half.

Edge 1 sends both by Edge sync over the Gateway Network (built in; set up by
scripts/sf-audit-alarms.sh). Edge 2 has no Gateway Network: its alarms ride
Sparkplug, and Sparkplug carries no audit records -- so pull() fetches Edge 2's
audit log over Ignition's REST API with a read-only token and files it in the
hub's EdgeAudit profile with the edge's own timestamps. That pull is OUR script
on a documented API, not an Edge feature, and the page says so.

evidence() puts each edge's local records beside the hub's for the console.
Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import Throwable as JThrowable

LOG = "SFAudit"
DB = "Postgres"
AUDIT_PROFILE = "EdgeAudit"
AUDIT_TABLE = "edge_audit_events"
JOURNAL_TABLE = "edge_alarm_events"
WD = "wd"
SITES = [
    {"key": "site1", "label": "Site1", "host": "ignition-edge1", "system": "Ignition-Edge1",
     "road": "Gateway Network: Edge sync sends audit and alarms (built in)"},
    {"key": "site2", "label": "Site2", "host": "ignition-edge2", "system": "Ignition-Edge2",
     "road": "No Gateway Network: alarms by MQTT; audit pulled over REST (our script)"},
]
PULL_SITE = SITES[1]
TOKEN_NAME = "hub-audit-reader"
KEY_NAME = "edge2-audit-token"
EDGE_PROFILE = "EdgeAuditProfile"
FIRST_PULL_HOURS = 24
WINDOW_HOURS = 2
SHOW = 8
SIGN_INS = ("Login Request", "Login Response", "Logout")


def _logger():
    return system.util.getLogger(LOG)


def _secret(name):
    with system.secrets.readSecretValue(WD, name) as plain:
        return str(plain.getSecretAsString())


def _http():
    return system.net.httpClient(timeout=4000, version="HTTP_1_1")


def _hub_high_water(system_name):
    """Epoch ms of the newest record from this edge already at the hub, or None."""
    v = system.db.runScalarPrepQuery(
        "SELECT max(event_timestamp) FROM %s WHERE originating_system LIKE ?" % AUDIT_TABLE,
        ["%" + system_name + "%"], DB)
    return long(v.getTime()) if v is not None else None


def pull():
    """Copy Edge 2's new audit records to the hub. Returns how many. Never raises.

    Resumes from the newest Edge 2 record already filed, inclusive, and skips
    the ones at exactly that millisecond that are already there -- so a record
    is never filed twice and none is skipped for sharing a timestamp."""
    site = PULL_SITE
    try:
        key = _secret(KEY_NAME)
    except (Exception, JThrowable), e:
        _logger().debug("no %s secret yet: %s" % (KEY_NAME, e))
        return 0
    try:
        hw = _hub_high_water(site["system"])
        start = hw if hw is not None else system.date.toMillis(
            system.date.addHours(system.date.now(), -FIRST_PULL_HOURS))
        url = "http://%s:8088/data/api/v1/audit/log/%s" % (site["host"], EDGE_PROFILE)
        resp = _http().get(url, params={"startTime": str(start), "limit": "500",
                                        "sortBy": "asc(timestamp)"},
                           headers={"X-Ignition-API-Token": "%s:%s" % (TOKEN_NAME, key)})
        if not resp.good:
            _logger().warn("audit pull from %s: HTTP %d" % (site["label"], resp.statusCode))
            return 0
        items = (resp.json or {}).get("items") or []
        seen = set()
        if hw is not None:
            ds = system.db.runPrepQuery(
                "SELECT action, action_target FROM %s WHERE originating_system LIKE ? "
                "AND event_timestamp = ?" % AUDIT_TABLE,
                ["%" + site["system"] + "%", system.date.fromMillis(hw)], DB)
            seen = set((r[0], r[1]) for r in ds)
        n = 0
        for it in items:
            ts = long(it.get("timestamp") or 0)
            if hw is not None and (ts < hw or (ts == hw and (it.get("action"), it.get("actionTarget")) in seen)):
                continue
            system.util.audit(action=it.get("action"), actionTarget=it.get("actionTarget"),
                              actionValue=it.get("actionValue"), auditProfile=AUDIT_PROFILE,
                              actor=it.get("actor") or "Unknown",
                              actorHost=it.get("actorHost") or "Unknown",
                              originatingSystem=["source", site["system"], "road", "REST pull"],
                              eventTimestamp=system.date.fromMillis(ts),
                              originatingContext=int(it.get("originatingContext") or 1),
                              statusCode=int(it.get("statusCode") or 0))
            n += 1
        if n:
            _logger().info("audit pull from %s: %d record(s) filed in %s" % (site["label"], n, AUDIT_PROFILE))
        return n
    except (Exception, JThrowable), e:
        _logger().warn("audit pull from %s failed: %s" % (site["label"], e))
        return 0


# --- the evidence tables -------------------------------------------------------

def _local(site, since):
    try:
        resp = _http().get("http://%s:8088/system/webdev/Edge/sf/local" % site["host"],
                           params={"since": str(since)})
        if resp.good:
            return resp.json or {}, ""
        return {}, "HTTP %d" % resp.statusCode
    except (Exception, JThrowable), e:
        return {}, "not answering (%s)" % str(e)[:60]


def _hhmmss(ms):
    return system.date.format(system.date.fromMillis(long(ms)), "HH:mm:ss")


def _audit_table(site, local, since, show):
    ds = system.db.runPrepQuery(
        "SELECT event_timestamp, action, action_target FROM %s WHERE originating_system LIKE ? "
        "AND event_timestamp >= ?" % AUDIT_TABLE,
        ["%" + site["system"] + "%", system.date.fromMillis(since)], DB)
    hub = set((long(r[0].getTime()), r[1], r[2]) for r in ds)
    rows, matched = [], 0
    # Sign-ins are left out: every API session (the demo's own tooling, the
    # trial keeper) makes a pair, and they buried the records a customer is
    # shown. The count line says so.
    recs = sorted([r for r in (local.get("audit") or []) if r.get("Action") not in SIGN_INS],
                  key=lambda r: -long(r.get("Timestamp") or 0))
    for r in recs:
        k = (long(r.get("Timestamp") or 0), r.get("Action"), r.get("Action Target"))
        at = k in hub
        matched += 1 if at else 0
        if len(rows) < show:
            rows.append({"when": _hhmmss(k[0]), "what": "%s (%s)" % (k[1], r.get("Actor") or "?"),
                         "local": u"✓", "hub": u"✓" if at else "missing"})
    return rows, len(recs), matched, len(hub)


def _alarm_table(site, local, since, show):
    ds = system.db.runPrepQuery(
        "SELECT DISTINCT eventid FROM %s WHERE eventtime >= ?" % JOURNAL_TABLE,
        [system.date.fromMillis(since)], DB)
    hub = set(r[0] for r in ds)
    states = {0: "active", 1: "cleared", 2: "acknowledged"}
    rows, matched = [], 0
    recs = [r for r in (local.get("alarms") or []) if not r.get("IsSystemEvent")]
    recs.sort(key=lambda r: -long(r.get("EventTime") or 0))
    for r in recs:
        at = r.get("EventId") in hub
        matched += 1 if at else 0
        if len(rows) < show:
            src = unicode(r.get("Source") or "")
            rows.append({"when": _hhmmss(r.get("EventTime") or 0),
                         "what": "%s %s" % (src.split("/alm:")[-1] if "/alm:" in src else src,
                                            states.get(r.get("EventState"), r.get("EventState"))),
                         "local": u"✓", "hub": u"✓" if at else "missing"})
    return rows, len(recs), matched


def evidence(show=SHOW):
    """{site1: {...}, site2: {...}} for the Store & Forward evidence tables.
    Each: road, audit/alarms rows (newest first, SHOW of them) and a count line.
    Never raises."""
    since = system.date.toMillis(system.date.addHours(system.date.now(), -WINDOW_HOURS))
    out = {}
    for site in SITES:
        block = {"label": site["label"], "road": site["road"], "audit": [], "alarms": [],
                 "auditLine": u"—", "alarmLine": u"—"}
        local, err = _local(site, since)
        if err:
            block["auditLine"] = block["alarmLine"] = "%s %s" % (site["label"], err)
            out[site["key"]] = block
            continue
        try:
            rows, n, m, _ = _audit_table(site, local, since, show)
            block["audit"] = rows
            block["auditLine"] = "%d local, %d at the hub, %d missing (last %d h; sign-ins not shown)" % (n, m, n - m, WINDOW_HOURS)
        except (Exception, JThrowable), e:
            block["auditLine"] = "hub audit unreadable: %s" % str(e)[:80]
        try:
            rows, n, m = _alarm_table(site, local, since, show)
            block["alarms"] = rows
            block["alarmLine"] = "%d local, %d at the hub, %d missing (last %d h)" % (n, m, n - m, WINDOW_HOURS)
        except (Exception, JThrowable), e:
            block["alarmLine"] = "hub journal unreadable: %s" % str(e)[:80]
        out[site["key"]] = block
    return out


def act(site_key, kind):
    """Ask an edge to make a record (kind audit|alarm|clear), over HTTP for both
    edges -- Edge 2 must not need the Gateway Network. Returns a message."""
    site = next((s for s in SITES if s["key"] == site_key), None)
    if site is None:
        return "no such site"
    try:
        token = _secret("sf-token")
    except (Exception, JThrowable):
        return "no sf-token on this gateway -- scripts/sf-audit-alarms.sh installs it"
    try:
        resp = _http().post("http://%s:8088/system/webdev/Edge/sf/action" % site["host"],
                            headers={"Content-Type": "application/json", "X-WD-Token": token},
                            data={"kind": kind})
        j = (resp.json or {}) if resp.good else {}
        return "%s: %s" % (site["label"], j.get("message") or "HTTP %d" % resp.statusCode)
    except (Exception, JThrowable), e:
        return "%s: not answering (%s)" % (site["label"], str(e)[:60])
