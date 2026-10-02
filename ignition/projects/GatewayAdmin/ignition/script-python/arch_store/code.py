"""Make an Architecture Builder drawing survive a page reload.

The builder keeps its canvas in `props.nodes` and `props.edges`, which are
component props -- per session, and gone the moment the page is reloaded. Undo
history lives in `view.custom` and goes the same way. So the tab was a sketchpad
that silently wiped itself, which is the worst kind of drawing tool: it looks
like it kept your work right up until it did not.

WHY A TAG AND NOT A SESSION PROP OR A PROJECT RESOURCE

A session prop dies with the session, which is the problem. A project resource
would be durable but is not writable at runtime without rewriting a file on the
gateway and rescanning -- which this repo does exactly once, for the style pack,
because that one has to travel over EAM. A drawing does not: it is this hub's
own scratch space. A memory tag is durable, gateway-scoped, shared between
sessions, and free to write.

WHY NOT A BINDING

Binding `props.nodes` to the tag would make the canvas read-only in practice: a
bound property cannot be written, so the first drag would fail. Load once on
startup and save on change instead -- the same shape the style switcher uses to
avoid the identical trap.
"""

# Jython 2.7 -- `except X, e`, not `as e`.

from java.lang import Throwable as JThrowable

PROVIDER = "default"
FOLDER = "Architecture"
TAG = "Canvas"

PATH = "[%s]%s/%s" % (PROVIDER, FOLDER, TAG)


def _logger():
    return system.util.getLogger("ArchStore")


def _ensure():
    """Create the memory tag if it is not there. Returns True if usable.

    Checked every call rather than once at startup: the tag lives in the
    gateway's own provider, not in the project, so it does NOT travel with a
    deploy and does not exist at all on a freshly built gateway.
    """
    try:
        if system.tag.exists(PATH):
            return True
        system.tag.configure("[%s]%s" % (PROVIDER, FOLDER), [{
            "name": TAG,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "String",
            "value": "",
        }], "i")
        _logger().info("created %s" % PATH)
        return True
    except (Exception, JThrowable), e:
        _logger().warn("cannot create %s: %s" % (PATH, e))
        return False


def save(nodes, edges):
    """Persist the canvas. Never raises -- it runs from a property-change script,
    and one that throws would break editing rather than just persistence."""
    if not _ensure():
        return False
    try:
        # The props are Perspective's own wrapper objects; round-tripping through
        # the JSON encoder is what turns them into something writable, and is the
        # same thing the Export JSON menu item does.
        payload = system.util.jsonEncode({
            "nodes": system.util.jsonDecode(system.util.jsonEncode(nodes)),
            "edges": system.util.jsonDecode(system.util.jsonEncode(edges)),
        })
        system.tag.writeBlocking([PATH], [payload])
        return True
    except (Exception, JThrowable), e:
        _logger().warn("cannot save the canvas: %s" % e)
        return False


def load():
    """The stored canvas as (nodes, edges), or (None, None) if there is none.

    (None, None) rather than empty dicts on purpose: the caller must be able to
    tell "nothing stored" from "stored, and empty". Restoring {} over a canvas
    the user had just drawn would be the same bug as not persisting at all.
    """
    if not _ensure():
        return (None, None)
    try:
        qv = system.tag.readBlocking([PATH])[0]
        if qv is None or qv.value is None:
            return (None, None)
        raw = str(qv.value).strip()
        if not raw:
            return (None, None)
        blob = system.util.jsonDecode(raw)
        return (blob.get("nodes"), blob.get("edges"))
    except (Exception, JThrowable), e:
        _logger().warn("cannot load the canvas: %s" % e)
        return (None, None)


def clear():
    """Forget the stored canvas."""
    if not _ensure():
        return False
    try:
        system.tag.writeBlocking([PATH], [""])
        return True
    except (Exception, JThrowable), e:
        _logger().warn("cannot clear the canvas: %s" % e)
        return False
