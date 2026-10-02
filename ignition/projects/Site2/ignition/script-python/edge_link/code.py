"""Cutting an edge's Gateway Network link -- from the edge, on a timer it owns.

The MQTT edge is cut at the BROKER, by banning its peer address, and that works
because the broker is a third party: it is still there to lift the ban, and the
ban carries an `until` so a forgotten cut heals itself.

The Gateway Network edge has no third party. The link IS the road, so anything
the hub does to break it also throws away the means of putting it back:

  * Revoking the incoming connection's approval is a SECURITY action, not a
    network one, and it is how you lose the road you would need to give it back.
  * Disabling remote history sync stops the forwarding but not the live values,
    so the card would still say STREAMING while the trend went flat -- a screen
    that contradicts itself in the middle of a demonstration.

So the edge cuts itself, and -- this is the whole design -- the edge is the only
thing that can restore it. The hub asks over the Gateway Network *while the link
is still up*; the edge writes a deadline, drops the connection, and brings it
back when the deadline passes. The same shape as the MQTT cut's deadline in
wd-control, for the same reason: a cut nobody can reach must be time-boxed.

**The deadline is a TAG, not a module global.** Gateway scripts restart on every
trial top-up -- about every ten minutes here -- and a restart mid-cut would
otherwise forget to restore, leaving the edge off the network with no way back
in but the Designer. A memory tag survives both a script restart and a gateway
restart, and `tick()` restores from it the moment scripts run again.

Lives in each Site because EAM carries a Site's own resources to its edge as
part of the flattened `Edge` project. Every entry point is a no-op where there
is no Gateway Network connection to cut, so the same resource sits harmlessly on
the hub.

Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import Throwable as JThrowable

LOG = "EdgeLink"

# The resource that defines the edge's road to the hub. One per outgoing
# connection; the demo has exactly one, but nothing here assumes that.
CONN_MODULE = "ignition"
CONN_TYPE = "gateway-network-outgoing"

# Where the restore deadline lives. Deliberately OUTSIDE `MQTT Tags`, which is
# the path MQTT Transmission is configured to watch -- a control value has no
# business being published as process data.
DEADLINE_PROVIDER = "[edge]"
DEADLINE_FOLDER = "SF Demo"
DEADLINE_NAME = "LinkRestoreAt"
DEADLINE_TAG = "%s%s/%s" % (DEADLINE_PROVIDER, DEADLINE_FOLDER, DEADLINE_NAME)

# Longest cut we will accept. The page offers 1-5 minutes; this is the backstop
# against a bad payload asking for a cut nobody is around to wait out.
MAX_SECONDS = 900


def _logger():
    return system.util.getLogger(LOG)


def _now():
    from java.lang import System as JSystem
    return JSystem.currentTimeMillis()


def _config_manager():
    from com.inductiveautomation.ignition.gateway import IgnitionGateway
    return IgnitionGateway.get().getConfigurationManager()


def _resource_type():
    from com.inductiveautomation.ignition.common.resourcecollection import ResourceType
    return ResourceType(CONN_MODULE, CONN_TYPE)


def connections():
    """Every outgoing Gateway Network connection this gateway defines.

    Empty on a gateway that dials out to nobody -- the hub -- which is what
    makes every function here safe to call anywhere.
    """
    try:
        return list(_config_manager().getResources(_resource_type()))
    except (Exception, JThrowable), e:
        _logger().warn("could not list outgoing connections: %s" % e)
        return []


def available():
    return len(connections()) > 0


def _set_enabled(enabled):
    """Enable or disable every outgoing connection. Returns how many changed.

    `enabled` is a resource ATTRIBUTE, alongside uuid and lastModification --
    not a field of config.json -- so this rewrites the attribute and pushes the
    resource back under its own signature. The signature is the optimistic lock:
    push a stale one and the gateway answers `PushException: MODIFY illegal:
    signature mismatch`, so it is re-read immediately before every push rather
    than cached.
    """
    from com.inductiveautomation.ignition.common.gson import JsonPrimitive
    from com.inductiveautomation.ignition.common.resourcecollection import ChangeOperation
    from com.inductiveautomation.ignition.common.resourcecollection import ResourceSignature
    from java.lang import Boolean as JBoolean
    from java.util import ArrayList
    from java.util.concurrent import TimeUnit

    # An explicit JsonPrimitive rather than the (String, boolean) overload:
    # Python's bool is a subclass of int, so Jython can bind it to putAttribute's
    # INT overload instead and persist `"enabled": 1`, which is not the same
    # thing to a reader calling getAsBoolean.
    value = JsonPrimitive(JBoolean(enabled))

    ops = ArrayList()
    for resource in connections():
        current = resource.getAttribute("enabled")
        # Optional<JsonElement>, and absent means enabled. Read through str()
        # rather than getAsBoolean(): the declared type is JsonElement but Jython
        # hands back a plain Python bool here, so getAsBoolean() raises
        # AttributeError -- which the timer swallowed into a log line once a
        # second while the page cheerfully showed LINK CUT for a link that was
        # never dropped. str() is "true"/"True" either way.
        if current is not None and current.isPresent():
            if (str(current.get()).strip().lower() == "true") == enabled:
                continue
        elif enabled:
            continue
        # getDefiningCollectionName, NOT getCollectionName. A resource read out
        # of the merged config view reports the OVERLAY collection it was read
        # through (`local`), not the one that actually defines it (`core`), and
        # a push aimed at the overlay is rejected with a message that reads as
        # though the resource were missing rather than misfiled:
        #   PushException: MODIFY illegal: 'ResourceId{resourcePath=ignition/
        #   gateway-network-outgoing/<name>, collectionName=local}' doesn't exist
        # -- while the REST API cheerfully lists that same resource in `core`.
        # Neither copyWithAttribute nor setting getCollectionName() back by hand
        # fixes it, because both preserve the wrong name.
        updated = (resource.toBuilder()
                   .setResourceCollectionName(resource.getDefiningCollectionName())
                   .putAttribute("enabled", value)
                   .build())
        # The SIGNATURE carries a ResourceId too, and it is stamped with the same
        # overlay collection -- so passing the original one straight through
        # swaps "doesn't exist" for "signature mismatch", which looks like a
        # stale read and is not one. Rebuild it against the corrected id,
        # keeping the original bytes as the optimistic lock.
        signature = ResourceSignature(updated.getResourceId(),
                                      resource.getResourceSignature().signature())
        ops.add(ChangeOperation.newModifyOp(updated, signature))
    if ops.isEmpty():
        return 0
    try:
        # Waited on rather than fired and forgotten: push returns a future, and
        # a rejected write would otherwise look exactly like a successful one --
        # which is the failure mode that made every trial reset appear to work.
        _config_manager().push(ops).get(10, TimeUnit.SECONDS)
    except (Exception, JThrowable), e:
        _logger().warn("could not set outgoing connections enabled=%s: %s"
                       % (enabled, e))
        return 0
    return ops.size()


def _ensure_deadline_tag():
    """Create the folder and the tag, from the provider ROOT.

    Configuring against "[edge]SF Demo" directly would need that folder to exist
    already; declaring the folder inline creates both in one call. Merge rather
    than overwrite, so this never disturbs anything else under the provider.
    """
    if system.tag.exists(DEADLINE_TAG):
        return
    system.tag.configure(DEADLINE_PROVIDER, [{
        "name": DEADLINE_FOLDER,
        "tagType": "Folder",
        "tags": [{
            "name": DEADLINE_NAME,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "Int8",
            "value": 0,
        }],
    }], "m")


def _deadline():
    try:
        qv = system.tag.readBlocking([DEADLINE_TAG])[0]
    except (Exception, JThrowable):
        return 0
    if qv is None or qv.value is None:
        return 0
    try:
        return long(qv.value)
    except (TypeError, ValueError):
        return 0


def _set_deadline(millis):
    _ensure_deadline_tag()
    system.tag.writeBlocking([DEADLINE_TAG], [millis])


def remaining():
    """Milliseconds left on the current cut, or 0 if the link is up."""
    return max(0, _deadline() - _now())


def cut(seconds):
    """Arm a cut of `seconds`. The timer does the disconnecting a beat later.

    **It only writes the deadline. That is deliberate.** This is called over the
    very link it is about to break, so disconnecting here would drop the socket
    the reply has to travel back on: the hub would see a timeout and report a
    failure for something that had in fact worked. Writing a tag and letting the
    next `tick()` pull the plug puts the reply safely on the wire first, at the
    cost of up to one second before the link actually goes.
    """
    if not available():
        return "This gateway has no outgoing Gateway Network connection to cut."
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "Cut duration was not a number."
    seconds = max(1, min(MAX_SECONDS, seconds))

    _set_deadline(_now() + seconds * 1000)
    _logger().info("gateway network link cut armed for %ds" % seconds)
    return "Cut for %d seconds -- this edge will restore itself." % seconds


def restore():
    """Bring the link back now and clear the deadline."""
    _set_deadline(0)
    changed = _set_enabled(True)
    if changed:
        _logger().info("gateway network link restored")
    return "Gateway Network link restored."


def tick():
    """Apply and then lift the cut. Called every second by the edge's timer.

    Idempotent and cheap: with no cut outstanding this reads one tag and stops.

    It is also the RECOVERY path, and that is the reason the deadline is a tag.
    A gateway that restarted -- or whose scripts were stopped by a lapsed trial,
    which happens here -- comes back with the connection still disabled, and
    restores on the first tick after the deadline rather than staying off the
    network until somebody opens a Designer.
    """
    deadline = _deadline()
    if not deadline:
        return False
    if _now() < deadline:
        # Re-asserted every tick rather than once, so a connection re-enabled by
        # anything else -- a config restore, a hand on the UI -- goes back down
        # for the rest of the window instead of quietly ending the outage.
        _set_enabled(False)
        return False
    restore()
    return True
