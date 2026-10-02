"""The PumpStation UDT: its definition, the variants the demo diverges it into, provisioning.

WHY A UDT AT ALL. The customer question this demo answers is about UDTs: "two
edge nodes, the same UDT on both, modify one of them -- what happens on Engine to
the UDT and to the instances of both edges? Do they sync?" So both edges build
their station from ONE definition, the alarms live on the definition's MEMBERS
(so diverging the definition can change alarm config too), and `apply()` changes
it on one edge while the other keeps the original.

Tags are gateway config, not project resources -- they live under
data/config/resources/core/ignition/tag-definition/, so a project deploy cannot
carry them. system.tag.configure is the mechanism, and everything here is
idempotent: safe from the setup script, from the Simulate timer's self-heal, and
from a button.

THREE TRAPS, all from the toolkit's scripting.md (verified 8.3.8):

- configure writes a UdtInstance OVER an existing Folder, answers Good, and every
  member then sits at Uncertain_InitialValue for ever, refusing writes. So a node
  that is not already an instance is deleted first.
- configure normalises alarms on the way in (`Equality` comes back `Equal`, a
  boolean setpoint comes back "1.0"), so every comparison below is semantic.
- configure half-writes without raising, so the quality codes it returns are
  read, not ignored.

And one of this module's own: configure is only called when something is
MISSING. Transmission watches its tag tree, and needless rewrites of a healthy
tree are exactly the kind of noise that makes a birth-certificate experiment
unreadable.

Jython 2.7 -- `except X, e`, not `as e`.
"""
import hashlib
import json

from java.lang import Throwable as JThrowable

import sp_site

TYPES = "[%s]_types_" % sp_site.PROVIDER

# The members the cloud is MEANT to write. Everything else is driven by the
# simulation, so a cloud write to it is overwritten within a second -- which is
# real Sparkplug behaviour (the edge-side source wins) rather than a bug.
WRITABLE = ("LevelSetpoint", "Mode")

# Display order. The definition itself is unordered.
ORDER = ("Level", "Inflow", "PumpRunning", "PumpFault", "DischargePressure",
         "LevelSetpoint", "Mode", "Heartbeat", "Vibration")


def _alarm(name, mode, setpoint, priority, notes, deadband=None):
    # Every transition goes to the edge's notification pipeline, which hands it
    # to the cloud (sp_notify). Active, clear and ack each have their own
    # property; an alarm with none of them set runs no pipeline at all.
    alarm = {"name": name, "mode": mode, "setpointA": setpoint,
             "priority": priority, "ackMode": "Manual", "notes": notes,
             "activePipeline": sp_site.PIPELINE_REF,
             "clearPipeline": sp_site.PIPELINE_REF,
             "ackPipeline": sp_site.PIPELINE_REF}
    if deadband:
        alarm["deadband"] = deadband
    return alarm


def _memory(name, data_type, value, unit=None, doc=None, alarms=None):
    tag = {"name": name, "tagType": "AtomicTag", "valueSource": "memory",
           "dataType": data_type, "value": value}
    if unit:
        tag["engUnit"] = unit
    if doc:
        tag["documentation"] = doc
    if alarms:
        tag["alarms"] = alarms
    return tag


def _members():
    """The shared definition's members. Fresh dicts on every call, so a variant
    can edit its copy without touching anybody else's."""
    return [
        _memory("Level", "Float8", 50.0, "%", "Wet-well level.", [
            _alarm("Level High", "AboveValue", 85.0, "High",
                   "Wet-well level above 85 %. Inflow is outrunning the pump -- "
                   "check it is running and not faulted. At 100 % the well overflows."),
            _alarm("Level Low", "BelowValue", 15.0, "Medium",
                   "Wet-well level below 15 %. The pump is close to running dry; "
                   "it stops on its own at the stop level."),
        ]),
        _memory("Inflow", "Float8", 0.0, "L/s", "Flow arriving at the wet well."),
        _memory("PumpRunning", "Boolean", False, None, "Duty pump running."),
        _memory("PumpFault", "Boolean", False, None,
                "Duty pump tripped. Latched until it is reset at the edge.", [
                    _alarm("Pump Fault", "Equal", 1, "Critical",
                           "The duty pump has tripped and will not restart until "
                           "the fault is reset at the edge. Level will now rise."),
                ]),
        _memory("DischargePressure", "Float8", 0.35, "bar", "Pump discharge pressure.", [
            # Deadband: with the well high the pressure rides 4.31 +/- 0.15 bar
            # (sp_sim's 7 s ripple), so without one it flapped across 4.4 every
            # cycle -- a notification storm, measured 11/09/2026. 0.3 still lets
            # the peaks raise it and clears it only once the well has drawn down.
            _alarm("Pressure High", "AboveValue", 4.4, "Medium",
                   "Discharge pressure above 4.4 bar. Check the discharge valve is "
                   "open and the rising main is not blocked.", deadband=0.3),
        ]),
        _memory("LevelSetpoint", "Float8", 50.0, "%",
                "Centre of the pump's start/stop band. Writable from the cloud."),
        _memory("Mode", "String", "AUTO", None,
                "AUTO or MANUAL. Writable from the cloud. MANUAL holds the pump stopped."),
        _memory("Heartbeat", "Int4", 0, None, "Counts once a second while the station runs."),
    ]


def _add_member(members):
    members.append(_memory("Vibration", "Float8", 1.8, "mm/s",
                           "A member that exists on THIS edge's definition only."))
    return members


def _alarm_setpoint(members):
    for member in members:
        for alarm in member.get("alarms", []):
            if alarm["name"] == "Level High":
                alarm["setpointA"] = 75.0
    return members


def _remove_member(members):
    return [m for m in members if m["name"] != "Inflow"]


def _datatype(members):
    for member in members:
        if member["name"] == "Level":
            member["dataType"] = "Int4"
            member["value"] = 50
    return members


def _default_value(members):
    for member in members:
        if member["name"] == "LevelSetpoint":
            member["value"] = 65.0
    return members


# name -> (what it does, in the words the page shows, transform). "base" is the
# shared definition both edges start from and the one udt_restore puts back.
VARIANTS = {
    "base": ("the shared definition both edges start from", None),
    "add_member": ("adds a Vibration member", _add_member),
    "alarm_setpoint": ("moves Level High from 85 % to 75 %", _alarm_setpoint),
    "remove_member": ("removes the Inflow member", _remove_member),
    "default_value": ("changes LevelSetpoint's default from 50 to 65", _default_value),
    "datatype": ("changes Level from Float8 to Int4", _datatype),
}


# The versioned-rollout fix (docs/SPARKPLUG.md T9): the changed layout gets its
# own NAME, so Engine keeps one _types_ entry per shape instead of one per name.
V2 = sp_site.UDT + "_v2"
V2_VARIANT = "add_member"
VERSIONS = {1: (sp_site.UDT, "base"), 2: (V2, V2_VARIANT)}


def type_path(name=None):
    return "%s/%s" % (TYPES, name or sp_site.UDT)


def definition(variant="base", name=None):
    if variant not in VARIANTS:
        raise ValueError("unknown variant '%s' -- one of: %s"
                         % (variant, ", ".join(sorted(VARIANTS))))
    members = _members()
    transform = VARIANTS[variant][1]
    if transform is not None:
        members = transform(members)
    return {"name": name or sp_site.UDT, "tagType": "UdtType", "tags": members}


# --- writing ------------------------------------------------------------------

def _configure(base, tags, policy):
    results = system.tag.configure(base, tags, policy)
    bad = [str(q) for q in results if not q.isGood()]
    if bad:
        raise ValueError("configure %s refused: %s" % (base, ", ".join(bad)))


def _tag_type(path):
    """'UdtInstance', 'Folder', ... for an existing node; None when absent."""
    try:
        if not system.tag.exists(path):
            return None
        config = system.tag.getConfiguration(path, False)
        return str(config[0].get("tagType")) if config else "unknown"
    except (Exception, JThrowable), e:
        sp_site.logger().warn("cannot read the type of %s: %s" % (path, e))
        return "unknown"


def instance_type_id():
    """The typeId this edge's instance is bound to, or None when there is none."""
    try:
        if not system.tag.exists(sp_site.instance_path()):
            return None
        config = system.tag.getConfiguration(sp_site.instance_path(), False)
        type_id = config[0].get("typeId") if config else None
        return str(type_id) if type_id else None
    except (Exception, JThrowable), e:
        sp_site.logger().warn("cannot read the typeId of %s: %s" % (sp_site.instance_path(), e))
        return None


def _wanted_type():
    return instance_type_id() or sp_site.UDT


def definitions():
    """The PumpStation* definitions this edge holds, by name."""
    try:
        return sorted(str(n["name"]) for n in system.tag.browse(TYPES).getResults()
                      if str(n["name"]).startswith(sp_site.UDT))
    except (Exception, JThrowable):
        return []


def provision():
    """Definition, folders and this edge's instance -- whatever is missing.

    Never rewrites a definition that already exists: that would silently undo a
    deliberate divergence. udt_restore is the way back to the shared one.
    The definition checked is the one the instance is bound to, so an edge that
    has moved to PumpStation_v2 and retired PumpStation is not given it back.
    Returns the list of things it created (empty when all was present).
    """
    done = []
    wanted = _wanted_type()
    if not system.tag.exists(type_path(wanted)):
        variant = V2_VARIANT if wanted == V2 else "base"
        _configure(TYPES, [definition(variant, wanted)], "o")
        done.append("definition %s" % wanted)

    folder = "[%s]%s" % (sp_site.PROVIDER, sp_site.FOLDER)
    if not system.tag.exists(sp_site.device_path()):
        _configure("[%s]" % sp_site.PROVIDER,
                   [{"name": sp_site.FOLDER, "tagType": "Folder",
                     "tags": [{"name": sp_site.DEVICE, "tagType": "Folder"}]}], "m")
        done.append("folders %s/%s" % (folder, sp_site.DEVICE))

    instance = sp_site.instance_path()
    kind = _tag_type(instance)
    if kind is not None and kind != "UdtInstance":
        # The dead-instance trap: never configure an instance over this.
        system.tag.deleteTags([instance])
        done.append("removed a %s that stood where the instance goes" % kind)
        kind = None
    if kind is None:
        _configure(sp_site.device_path(),
                   [{"name": sp_site.site_name(), "tagType": "UdtInstance",
                     "typeId": sp_site.UDT}], "o")
        done.append("instance %s" % instance)

    # The notification tags (sp_notify): EdgeNotice/CloudNotice beside the
    # station, and the bounded log outside the published folder.
    import sp_notify
    done.extend("notification tag %s" % n for n in sp_notify.ensure_tags())

    if done:
        sp_site.logger().info("provisioned: %s" % "; ".join(done))
    return done


def ensure_present():
    """The Simulate timer's self-heal: cheap existence checks, provision on a miss.

    It is what lets a rebuilt edge come back on its own after a deploy, without
    anyone re-running the setup script.
    """
    import sp_notify
    if (system.tag.exists(sp_site.instance_path()) and system.tag.exists(type_path(_wanted_type()))
            and system.tag.exists(sp_notify.notice_path())
            and system.tag.exists(sp_notify.log_path())):
        return []
    return provision()


def apply(variant, name=None):
    """Rewrite this edge's definition `name` (PumpStation) as `variant`. Returns a sentence.

    Whether an overwrite drops a member the new definition leaves out is not
    something to rely on, so any member the variant does not name is deleted
    explicitly. That is what makes remove_member real, and what makes a restore
    after add_member take Vibration away again.
    """
    new = definition(variant, name)
    _configure(TYPES, [new], "o")
    wanted = set(m["name"] for m in new["tags"])
    stale = []
    try:
        for node in system.tag.browse(type_path(name)).getResults():
            if str(node["name"]) not in wanted:
                stale.append(str(node["fullPath"]))
    except (Exception, JThrowable), e:
        sp_site.logger().warn("cannot browse %s: %s" % (type_path(), e))
    if stale:
        system.tag.deleteTags(stale)
    _CURRENT["at"] = 0
    sp_site.logger().info("definition %s is now '%s'%s" % (
        new["name"], variant, " (removed %s)" % ", ".join(stale) if stale else ""))
    return "%s on %s is now '%s' -- %s." % (
        new["name"], sp_site.system_name(), variant, VARIANTS[variant][0])


def use_version(version):
    """Bind this edge's instance to version 1 (PumpStation) or 2 (PumpStation_v2).

    Creates the target definition when this edge lacks it, never rewrites one it
    has. Returns a sentence. The instance is re-pointed in place with a merge;
    the member values are read back afterwards because an instance configured
    over the wrong thing goes dead without raising (see THREE TRAPS above).
    """
    name, variant = VERSIONS[int(version)]
    notes = []
    if not system.tag.exists(type_path(name)):
        _configure(TYPES, [definition(variant, name)], "o")
        notes.append("defined %s" % name)
    before = instance_type_id()
    if before != name:
        _configure(sp_site.device_path(),
                   [{"name": sp_site.site_name(), "tagType": "UdtInstance", "typeId": name}], "m")
        notes.append("instance moved %s -> %s" % (before, name))
    _CURRENT["at"] = 0
    if not notes:
        return "%s on %s already uses %s." % (sp_site.instance_path(), sp_site.system_name(), name)
    sp_site.logger().info("use_version %s: %s" % (version, "; ".join(notes)))
    return "%s: %s." % (sp_site.system_name(), "; ".join(notes))


def retire(name):
    """Delete definition `name` from this edge, unless the instance still uses it."""
    if instance_type_id() == name:
        raise ValueError("%s is still used by %s" % (name, sp_site.instance_path()))
    if not system.tag.exists(type_path(name)):
        return "%s holds no %s." % (sp_site.system_name(), name)
    system.tag.deleteTags([type_path(name)])
    _CURRENT["at"] = 0
    sp_site.logger().info("retired definition %s" % name)
    return "%s deleted %s." % (sp_site.system_name(), name)


# --- reading ------------------------------------------------------------------

def _plain(value):
    """A tag-config value as plain JSON-able Python, whatever Java handed back."""
    if value is None or isinstance(value, (bool, int, long, float)):
        return value
    if isinstance(value, basestring):
        return unicode(value)
    if isinstance(value, dict) or hasattr(value, "keySet"):
        keys = value.keys() if isinstance(value, dict) else list(value.keySet())
        return dict((str(k), _plain(value[k] if isinstance(value, dict) else value.get(k)))
                    for k in keys)
    if isinstance(value, (list, tuple)) or hasattr(value, "iterator"):
        try:
            return [_plain(v) for v in value]
        except (Exception, JThrowable):
            pass
    return unicode(value)


def _live_definition():
    """The definition this edge's instance is bound to, as plain JSON, or None."""
    path = type_path(_wanted_type())
    try:
        text = system.tag.exportTags(tagPaths=[path], recursive=True)
        doc = json.loads(text)
        if doc.get("tagType") != "UdtType" and doc.get("tags"):
            doc = doc["tags"][0]
        return doc
    except (Exception, JThrowable), e:
        sp_site.logger().debug("exportTags unavailable (%s); using getConfiguration" % e)
    try:
        config = system.tag.getConfiguration(path, True)
        return _plain(config[0]) if config else None
    except (Exception, JThrowable), e:
        sp_site.logger().warn("cannot read %s: %s" % (path, e))
        return None


def _number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(str(value))
    except (ValueError, TypeError):
        return unicode(value)


def _value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, basestring) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return _number(value)


def _projection(node):
    """The parts of a definition that matter to the comparison, normalised.

    Only what configure is known to preserve: member names, data types, default
    values, and each alarm's name, mode, setpoint and priority. `Equality` and
    `Equal` are one mode; a boolean setpoint and "1.0" are one number.
    """
    members = []
    for member in (node or {}).get("tags", []) or []:
        alarms = []
        for alarm in member.get("alarms", []) or []:
            mode = unicode(alarm.get("mode", ""))
            alarms.append({"name": unicode(alarm.get("name", "")),
                           "mode": "Equal" if mode == "Equality" else mode,
                           "setpointA": _number(alarm.get("setpointA")),
                           "priority": unicode(alarm.get("priority", "Low"))})
        members.append({"name": unicode(member.get("name", "")),
                        "dataType": unicode(member.get("dataType", "")),
                        "value": _value(member.get("value")),
                        "alarms": sorted(alarms, key=lambda a: a["name"])})
    return sorted(members, key=lambda m: m["name"])


def _digest(projection):
    return hashlib.sha1(json.dumps(projection, sort_keys=True)).hexdigest()


# Recomputed at most every few seconds: the observer endpoint is polled, and the
# definition changes only when somebody presses a button (apply() clears it).
_CURRENT = {"at": 0, "value": None}


def current():
    """What this edge's definition IS right now -- the variant it matches, if any."""
    from java.lang import System as JSystem
    now = JSystem.currentTimeMillis()
    if _CURRENT["value"] is not None and now - _CURRENT["at"] < 3000:
        return _CURRENT["value"]

    live = _projection(_live_definition())
    variant = "custom" if live else "absent"
    for name in VARIANTS:
        if _projection(definition(name)) == live:
            variant = name
            break

    def rank(member):
        name = member["name"]
        return (ORDER.index(name) if name in ORDER else len(ORDER), name)

    type_id = instance_type_id()
    value = {
        "name": type_id or sp_site.UDT,
        "typeId": type_id,
        "definitions": definitions(),
        "path": type_path(type_id),
        "variant": variant,
        "description": VARIANTS[variant][0] if variant in VARIANTS else "",
        "hash": ("sha1-" + _digest(live)) if live else None,
        "instancePath": sp_site.instance_path(),
        "instanceType": _tag_type(sp_site.instance_path()),
        "members": [{"name": m["name"], "dataType": m["dataType"],
                     "alarms": [a["name"] for a in m["alarms"]],
                     "setpoints": dict((a["name"], a["setpointA"]) for a in m["alarms"])}
                    for m in sorted(live, key=rank)],
        "variants": sorted(VARIANTS),
    }
    _CURRENT["at"] = now
    _CURRENT["value"] = value
    return value


def alarm_notes():
    """alarm name -> notes, from the shared definition (what an operator reads)."""
    notes = {}
    for member in _members():
        for alarm in member.get("alarms", []):
            notes[alarm["name"]] = alarm["notes"]
    return notes
