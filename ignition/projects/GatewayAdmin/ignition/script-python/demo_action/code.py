"""One in-flight latch for every action button on this console.

WHY THIS EXISTS. Every action on the console used to be a bare
`ia.input.button` with no `props.enabled`: clickable throughout the one to three
minutes a demo takes to come up, refused by the control plane with a 409, and
the refusal printed in 12px grey at the very bottom of the page -- 700px from
the button that caused it. Told nothing, you click again.

So there is one latch and one binding shape, rather than a patch per button:

    props.enabled   {session.custom.acting} = '' && <that button's can flag>
    props.text      if({session.custom.acting} = '<key>',
                       <its busy label>, <its label>)
    the note label   if({session.custom.acting} = '<key>', {session.custom.actingWhat},
                        if(<can.ok>, if({session.custom.actKey} = '<key>',
                                        {session.custom.actNote}, ''),
                           <can.why>))

The note label sits BESIDE its own button, which is the half of the complaint
that a disabled button alone does not fix: a refusal has to be readable where
the click happened, and only for the button it belongs to.

THE WORK RUNS OFF THE HANDLER THREAD, on purpose and for two reasons. The
honest one is `gateway_admin.reset_trials()`, which can block ~28 seconds inside
a click handler with nothing on screen. The subtler one is that a handler which
sets a flag, works, and clears the flag has shown the flag to nobody -- the
click is one round trip, and the latch has to outlive it to be seen at all.

Jython 2.7: no f-strings, `except Exception, e:`, and `except Exception` does
NOT catch Java throwables, so anything touching Java names both.
"""
from java.lang import Throwable as JThrowable

LOG = "DemoAction"

# How long a latch may live before it is assumed lost. Longer than anything on
# this console legitimately takes with its own timeout (the slowest is
# reset_trials at 2 x 10s of Gateway Network request plus two 4s HTTP
# fallbacks), and short enough that a page nobody is watching is not left with
# every button dead. A stuck latch is the failure this whole module would
# otherwise introduce -- the buttons are disabled by one property, so anything
# that can leave that property set can disable the console.
LATCH_SECONDS = 45

# A destructive button asks twice. `stack.sh destroy` demands two different
# tokens on the command line; the page's equivalent is a second click, and this
# is how long the first one counts for. Short: an armed button that stays armed
# is a button that goes off when somebody comes back to the page and clicks
# what they think is a fresh control.
ARM_SECONDS = 12


def _logger():
    return system.util.getLogger(LOG)


def _spawn(fn):
    """Run fn off this thread, or on it if nothing will take it.

    Running it here as the last resort is deliberate: the action still happens
    and the latch still clears. What is lost is only that the latch was never
    seen, which is the state the console was in before this module existed.
    """
    try:
        system.util.invokeAsynchronous(fn)
        return
    except (Exception, JThrowable), e:
        _logger().warn("could not run an action off-thread: " + str(e))
    fn()


def _sleep(millis):
    from java.lang import Thread
    try:
        Thread.sleep(int(millis))
    except (Exception, JThrowable):
        pass


def _millis():
    from java.lang import System as JSystem
    return JSystem.currentTimeMillis()


def _get(session, name):
    """A session custom property as a string, and "" for anything unreadable.

    Everything this module compares is a key or an id, so one accessor that
    always answers a string keeps a Java Long read back out of a property tree
    from failing a comparison against the Python string that was written."""
    try:
        return str(getattr(session.custom, name) or "")
    except (Exception, JThrowable):
        return ""


def _set(session, name, value):
    """Never raises. A session that has gone away must not turn a finished
    action into a logged error, and must never leave the caller's `finally`
    half-run."""
    try:
        setattr(session.custom, name, value)
    except (Exception, JThrowable), e:
        _logger().debug("could not write session.custom." + name + ": " + str(e))


def busy(session):
    """Is an action in flight for this session."""
    return _get(session, "acting") != ""


def run(session, key, what, fn, *args):
    """Mark `key` in flight, run fn(*args) off-thread, and always clear.

    `what` is the plain-words present tense shown beside the button while it
    runs ("pushing Edge 1"), and `fn` must return the sentence to show when it
    is done -- every action function on this console already does, because each
    one is wired to a button and a silent failure there is what this stack keeps
    teaching.
    """
    if busy(session):
        # The button is disabled, so this is belt and braces -- and it is the
        # branch that matters if a click is already in the air when the latch
        # is taken, which is exactly the double-click this exists to stop.
        return

    ident = str(_millis())
    _set(session, "actId", ident)
    _set(session, "acting", key)
    _set(session, "actingWhat", what)
    _set(session, "actKey", key)
    _set(session, "actNote", "")
    _set(session, "armKey", "")

    def _go():
        note = ""
        try:
            note = fn(*args)
        except Exception, e:
            note = "that did not work: " + str(e)
            _logger().warn(key + " failed: " + str(e))
        except JThrowable, e:
            note = "that did not work: " + str(e)
            _logger().warn(key + " failed: " + str(e))
        _finish(session, key, ident, note)

    def _watchdog():
        _sleep(LATCH_SECONDS * 1000)
        _finish(session, key, ident,
                what + " has not reported back in " + str(LATCH_SECONDS)
                + "s -- the buttons come back on. It may still finish.",
                late=True)

    _spawn(_go)
    _spawn(_watchdog)


def _finish(session, key, ident, note, late=False):
    """Clear the latch, unless it is no longer ours.

    The id check is what keeps the watchdog from clearing a LATER action's
    latch: by the time it wakes, the click it was watching may be long done and
    another may be in flight."""
    if _get(session, "actId") != ident:
        return
    if late and _get(session, "acting") != key:
        return                      # it reported back on its own
    if note:
        _set(session, "actNote", str(note))
    _set(session, "acting", "")
    _set(session, "actingWhat", "")


# --- asking twice -------------------------------------------------------------

def arm(session, key, warning):
    """First click on a destructive button: arm it and say what will happen.

    Disarms itself, so a button left armed on a page nobody is watching goes
    back to being a button that has to be asked twice."""
    _set(session, "armKey", key)
    _set(session, "actKey", key)
    _set(session, "actNote", warning)

    def _expire():
        _sleep(ARM_SECONDS * 1000)
        if _get(session, "armKey") == key:
            _set(session, "armKey", "")

    _spawn(_expire)
    return warning


def armed(session, key):
    """Has this button already been clicked once, recently."""
    return _get(session, "armKey") == key


def note(session, key, text):
    """Say something beside `key`'s button without taking the latch.

    For an answer that arrives immediately -- a refusal the page can make on
    its own -- where taking the latch would only make the button flicker."""
    _set(session, "actKey", key)
    _set(session, "actNote", str(text))
    return text


# --- what a can flag looks like ----------------------------------------------

def can(ok, why="", label="", busy_label="", note=None):
    """One guard entry, as every `summary().can` on this console returns it.

    A pair, not a boolean, because a disabled button has to say why -- and the
    wording comes from the module that knows the reason rather than from an
    expression in a view, which is how the button and the card beside it are
    kept from disagreeing.

    `label`/`busy` ride along so the server owns the whole of the button's
    wording: the Store & Forward cut buttons have to count down, and a view
    expression that assembled that would be a second place the remaining time
    was worked out.

    `note` is what the line beside the button says when no click of its own is
    being reported -- the refusal by default, and occasionally a standing fact
    about a button that IS enabled ("its tab opens when it is ready"). It
    outranks the last click's answer, because it describes now rather than a
    moment ago.
    """
    why = "" if ok else str(why)
    return {"ok": bool(ok), "why": why, "label": str(label),
            "busy": str(busy_label),
            "note": why if note is None else str(note)}
