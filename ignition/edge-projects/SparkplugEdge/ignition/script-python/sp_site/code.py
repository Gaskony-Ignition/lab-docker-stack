"""Who this edge is and where its tags live -- every name the project uses, in one place.

The SAME project runs on every isolated edge (ignition-edge3 and ignition-edge4
today), so nothing in it may be specific to one of them. Identity is DERIVED
from the gateway's system name, which compose sets with `-n Ignition-Edge3`:

    system name     Ignition-Edge3
    Sparkplug node  Edge3            the name minus "Ignition-"
    site            North            SITES below; an unlisted node is its own site

scripts/sparkplug-setup.sh configures the transmitter's edgeNodeId by reading
`node` back from this project's observer endpoint rather than repeating the rule
in bash: one derivation, two consumers, so they cannot disagree.

WHY A DEVICE FOLDER ABOVE THE INSTANCE. The station is `[edge]AlarmDemo/Pumps/North`,
not `[edge]AlarmDemo/North`. Transmission makes each first-level folder under its
tag path a Sparkplug DEVICE, and with convertUdts=false a UDT instance is published
as a Template metric -- which the spec puts INSIDE a device. Keeping the instance
one level below the device folder means both transmitter modes publish the same
tree, and the UDT comparison is between like and like.

Jython 2.7 -- `except X, e`, not `as e`.
"""
import re

from java.lang import Throwable as JThrowable

PROVIDER = "edge"          # an Edge gateway has exactly one realtime provider
FOLDER = "AlarmDemo"       # the transmitter's tagPath
DEVICE = "Pumps"           # first folder under it -> the Sparkplug DEVICE id
GROUP = "AlarmDemo"        # the Sparkplug group both edges share
UDT = "PumpStation"
JOURNAL = "EdgeJournal"    # an Edge gateway has exactly one alarm journal
TOKEN_PROVIDER = "wd"      # the secret store scripts/sparkplug-setup.sh fills
# The edge's alarm notification pipeline (a project resource in this project,
# com.inductiveautomation.alarm-notification/alarm-pipelines/EdgeNotify). Its one
# Script block calls sp_notify.notify(event, PIPELINE) -- all logic stays here in
# the library, so the pipeline's binary data.bin never needs editing.
PIPELINE = "EdgeNotify"
# How an alarm names it. CommonAlarmProperties declares activePipeline /
# clearPipeline / ackPipeline as a QualifiedPath -- the form the pipeline
# runtime itself prints, "project:<project>:/pipeline:<name>". A tag STORES any
# string it is given, but a bare "EdgeNotify" or "Edge/EdgeNotify" never becomes
# a pipeline: the event is dropped before the pipeline manager logs anything,
# even at TRACE (measured 11/09/2026). An Edge gateway's one project is "Edge".
PIPELINE_PROJECT = "Edge"
PIPELINE_REF = "project:%s:/pipeline:%s" % (PIPELINE_PROJECT, PIPELINE)
TOKEN_NAME = "sparkplug-token"
LOG = "SparkplugEdge"

# Node id -> site name. The one per-edge fact in the project, kept as a table
# rather than as a file per edge.
SITES = {"Edge3": "North", "Edge4": "South"}

_CACHE = {}


def logger():
    return system.util.getLogger(LOG)


def system_name():
    """This gateway's system name. Cached: it cannot change without a restart."""
    if "name" in _CACHE:
        return _CACHE["name"]
    name = ""
    try:
        qv = system.tag.readBlocking(["[System]Gateway/SystemName"])[0]
        if qv.value:
            name = str(qv.value)
    except (Exception, JThrowable):
        pass
    if not name:
        # The system name is on the system properties manager, NOT on
        # IgnitionGateway itself (CLAUDE.md, Redundancy).
        try:
            from com.inductiveautomation.ignition.gateway import IgnitionGateway
            name = str(IgnitionGateway.get().getSystemPropertiesManager().getSystemName())
        except (Exception, JThrowable), e:
            logger().warn("cannot read the system name: %s" % e)
            return "Ignition-Unknown"
    _CACHE["name"] = name
    return name


def node_id():
    name = system_name()
    if name.lower().startswith("ignition-"):
        name = name[len("ignition-"):]
    return name


def site_name():
    node = node_id()
    return SITES.get(node, node)


def number():
    """The digits a node id ends with (3 for Edge3), or 0."""
    match = re.search(r"(\d+)$", node_id())
    return int(match.group(1)) if match else 0


def device_path():
    return "[%s]%s/%s" % (PROVIDER, FOLDER, DEVICE)


def instance_path():
    return "%s/%s" % (device_path(), site_name())


def member_path(member):
    return "%s/%s" % (instance_path(), member)


def headline():
    return u"Pump Station %s  ·  %s  ·  Sparkplug %s / %s" % (
        site_name(), system_name(), GROUP, node_id())


def shape():
    """(period s, phase s, amplitude factor) of this edge's inflow.

    Different per edge, because both run this one script: without it the two
    stations trace the same curve and a side-by-side view shows one line with
    the other hidden beneath it. Derived from the node number, so it needs no
    per-edge config. Tuned offline against the model in sp_sim: both edges cross
    Level High (85) and Level Low (15) every cycle -- North peaks ~93 % on a
    5-minute cycle, South ~92 % on a 3-minute one. A larger North amplitude
    pinned the well at 100 %, which reads as an overflow, not as a demonstration.
    """
    n = number()
    period = 180.0 + 40.0 * (n % 4)
    phase = float((n * 37) % int(period))
    amplitude = 1.0 - 0.08 * (n % 2)
    return period, phase, amplitude
