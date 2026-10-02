"""What this edge's MQTT Transmission module is set to, and the one lever it offers.

Read OFF DISK, not through an API. There is no system.* call that reports a
transmitter's settings in a form a WebDev endpoint can hand on, and the gateway
keeps each config resource as a plain config.json under
data/config/resources/<collection>/<module>/<type>/<name>/ -- the same reason
edge_stream.py in Site2 reads the edge's sync settings this way. `local`
overlays `core`, so it is read first.

These files are the gateway's OUTPUT, never an input: editing one does not
change the module (sparkplug-lab/docs/SETUP.md, step 5). Everything that
configures Transmission goes through the REST API in scripts/sparkplug-setup.sh.

Jython 2.7 -- `except X, e`, not `as e`.
"""
import json
import os

from java.lang import System as JSystem
from java.lang import Throwable as JThrowable

import sp_site

MODULE = "com.cirruslink.mqtt.transmission.gateway"
COLLECTIONS = ("local", "core")

_CACHE = {"at": 0, "value": None}


def _root():
    for prop in ("ignition.installdir", "user.dir"):
        base = JSystem.getProperty(prop)
        if base and os.path.isdir(os.path.join(base, "data", "config", "resources")):
            return os.path.join(base, "data", "config", "resources")
    return None


def _read(path):
    try:
        handle = open(path)
        try:
            return json.loads(handle.read())
        finally:
            handle.close()
    except (Exception, JThrowable):
        return None


def resources(type_id):
    """[{name, enabled, config}] for one Transmission resource type."""
    root = _root()
    found = {}
    if root is None:
        return []
    for collection in COLLECTIONS:
        base = os.path.join(root, collection, MODULE, type_id)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name in found:
                continue            # `local` already supplied this one
            config = _read(os.path.join(base, name, "config.json"))
            if config is None:
                continue
            meta = _read(os.path.join(base, name, "resource.json")) or {}
            enabled = (meta.get("attributes") or {}).get("enabled", True)
            found[name] = {"name": name, "enabled": bool(enabled), "config": config}
    return [found[k] for k in sorted(found)]


def transmitter():
    """The transmitter publishing this demo's group, else the first there is."""
    rows = resources("transmitter")
    for row in rows:
        if row["config"].get("groupId") == sp_site.GROUP:
            return row
    return rows[0] if rows else None


def _find(root, names, depth):
    """Full paths of tags under `root` whose name is one of `names`."""
    hits = []
    try:
        results = system.tag.browse(root).getResults()
    except (Exception, JThrowable):
        return hits
    for node in results:
        name = str(node["name"])
        path = str(node["fullPath"])
        if node.get("hasChildren") and depth > 0:
            hits.extend(_find(path, names, depth - 1))
        elif name in names:
            hits.append(path)
    return hits


def node_info_path(row=None):
    """The module's own status folder for THIS edge node, or None.

    Measured on 5.0.4 (tag export, 11/09/2026) -- the path embeds the transmitter
    name, group and node, so it is BUILT from the transmitter config every time
    rather than found once and cached: a cached path goes stale the moment the
    setup script renames the node, and then reads Bad_NotFound for ever:

        [MQTT Transmission]Transmission Info/Transmitters/<transmitter>/
            Edge Nodes/<groupId>/<edgeNodeId>/MQTT Client/Online
            Edge Nodes/<groupId>/<edgeNodeId>/Refresh Edge Node
    """
    row = row or transmitter()
    if not row:
        return None
    config = row["config"]
    if not config.get("groupId") or not config.get("edgeNodeId"):
        return None
    return "[MQTT Transmission]Transmission Info/Transmitters/%s/Edge Nodes/%s/%s" % (
        row["name"], config["groupId"], config["edgeNodeId"])


def connected(row=None):
    """(True/False/None, the tag it was read from). None = could not be read."""
    base = node_info_path(row)
    if not base:
        return None, None
    tag = base + "/MQTT Client/Online"
    try:
        qv = system.tag.readBlocking([tag])[0]
        if qv.quality.isGood():
            return bool(qv.value), tag
    except (Exception, JThrowable):
        pass
    return None, tag


def status():
    """The `transmission` block of the observer document."""
    now = JSystem.currentTimeMillis()
    if _CACHE["value"] is not None and now - _CACHE["at"] < 5000:
        return _CACHE["value"]
    row = transmitter() or {"name": None, "enabled": False, "config": {}}
    config = row["config"]
    servers = [s for s in resources("server") if s["enabled"]]
    is_connected, from_tag = connected()
    value = {
        "connected": is_connected,
        "connectedTag": from_tag,
        "transmitter": row["name"],
        "enabled": row["enabled"],
        "server": servers[0]["config"].get("url") if servers else None,
        "groupId": config.get("groupId"),
        "edgeNodeId": config.get("edgeNodeId"),
        "tagProvider": config.get("tagProvider"),
        "tagPath": config.get("tagPath"),
        "convertUdts": config.get("convertUdts"),
        "alarmEventEnable": config.get("alarmEventEnable"),
        "alarmJournalName": config.get("alarmJournalName"),
        "storeForward": bool(config.get("historyStore")),
        "historyStore": config.get("historyStore"),
    }
    _CACHE["at"] = now
    _CACHE["value"] = value
    return value


MODULE_REFRESH = "[MQTT Transmission]Transmission Control/Refresh"


def rebirth(scope="node"):
    """Ask Transmission to publish its birth certificates again. (ok, sentence).

    scope "node"   -- `Refresh Edge Node` for this node (the default).
    scope "module" -- `Transmission Control/Refresh`, module-wide.

    Why there are two (measured 11/09/2026): with convertUdts=false, after the
    PumpStation definition changed, `Refresh Edge Node` re-sent the CACHED
    Template -- the same definition md5 and the same eight-member instance --
    so a diverged UDT never reached the cloud through it. With convertUdts=true
    the same lever did carry the new member. docs/SPARKPLUG.md, T8.

    A UDT definition travels ONLY in NBIRTH, so after a definition changes the
    cloud learns of it at the next birth. Whether Transmission rebirths on its
    own after a tag-structure change is one of the things docs/SPARKPLUG.md
    records; this is the explicit lever either way. 5.0.4 has no tag called
    Rebirth: the per-node lever is `Refresh Edge Node` under this node's status
    folder, and `Transmission Control/Refresh` is the module-wide fallback.
    """
    base = node_info_path()
    tags = []
    if scope == "module":
        if system.tag.exists(MODULE_REFRESH):
            tags = [MODULE_REFRESH]
    elif base and system.tag.exists(base + "/Refresh Edge Node"):
        tags = [base + "/Refresh Edge Node"]
    elif system.tag.exists(MODULE_REFRESH):
        tags = [MODULE_REFRESH]
    if not tags:
        return False, "no Refresh Edge Node or Transmission Control/Refresh tag found"
    results = system.tag.writeBlocking(tags, [True] * len(tags))
    bad = [t for t, q in zip(tags, results) if not q.isGood()]
    if bad:
        return False, "rebirth write refused on %s" % ", ".join(bad)
    sp_site.logger().info("rebirth requested via %s" % ", ".join(tags))
    return True, "Rebirth requested on %s via %s." % (sp_site.system_name(), ", ".join(tags))
