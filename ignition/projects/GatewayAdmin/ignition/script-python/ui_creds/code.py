"""The login for a UI this stack cannot sign you in to.

The four Ignition gateways are opened ALREADY SIGNED IN -- the console's Open
button goes through a door that establishes the session server-side, and no
credential reaches the browser (control/autologin.py). Nginx Proxy Manager
has its own local login and no way in from outside, so for it the console can
only open the page. This is what stops
that becoming "and now go and find the password".

WHERE THE VALUES LIVE

The gateway's own secret store, provider `wd`, installed by
scripts/ign-secrets.sh from the same .secrets.env every stack is built from.
Read here with system.secrets and nowhere else.

They are NOT in this file, not in a view, and not in a binding -- and that is
the point rather than tidiness. Views are committed to git and EAM pushes them
to both edges, so a password written into one would be published twice over.

THE VALUE IS FETCHED ON THE CLICK, NEVER BOUND

A binding polls, so a bound password would be re-read every few seconds for as
long as the page is open, and would sit in the session's property tree where
anything can read it. These functions are called from a button and return the
value once; the popup drops it the moment it is closed.

A PyPlaintext OWNS ITS BUFFER. system.secrets hands back an object whose
lifecycle the caller manages -- hence the `with` block. Letting it fall out of
scope leaves the secret in memory until Java feels like collecting it.
"""
from java.lang import Throwable as JThrowable

PROVIDER = "wd"

# stack -> (what to call it, the secret pair's base name).
#
# Only the stacks whose UI has a login WE hold. A stack that is not here simply
# has no credential offered, which is the right answer for HAProxy's stats page
# -- it asks for none, and offering a button that reveals nothing is worse than
# no button.
UIS = {
    "npm": ("Proxy manager", "npm"),
}


def has_credential(stack):
    """Is there a stored login for this stack's UI?"""
    return stack in UIS


def label(stack):
    if stack in UIS:
        return UIS[stack][0]
    return stack


def _read(name):
    """One secret, as a str, with the plaintext buffer released afterwards.

    Every call here crosses into Java, so callers must catch JThrowable as well
    as Exception -- see the note on username() below. A PyPlaintext owns its
    buffer, which is why this is a `with` block rather than a bare call.
    """
    # getSecretAsString(), NOT getValue(). PyPlaintext exposes
    # getSecretAsString / getSecretAsBytes / getAsString / clear -- there is no
    # getValue, and Jython reports the miss as
    #   'com...secrets.Py' object has no attribute 'getValue'
    # which reads like the wrong object rather than the wrong method name.
    with system.secrets.readSecretValue(PROVIDER, name) as plain:
        return str(plain.getSecretAsString())


def username(stack):
    """The username. Not a secret -- stored beside the password only so the two
    cannot drift apart when somebody changes an admin email.

    Catches JThrowable as well as Exception. Jython's `except Exception` does
    not catch a Java throwable, and system.secrets raises Java ones: a missing
    secret therefore escaped this function, took summary() with it, and the
    popup rendered every field as a red ERROR box reading `null` -- with
    nothing in the gateway log, because the failure goes to the browser.
    """
    if stack not in UIS:
        return ""
    try:
        return _read(UIS[stack][1] + "-username")
    except (Exception, JThrowable), e:
        _log().warn("no username for %s: %s" % (stack, e))
        return ""


def password(stack):
    """The password, read now and returned once.

    Raises nothing: a popup that renders a Java stack trace in front of an
    audience is worse than one that says it could not read the secret. The
    reason goes to the gateway log, where it is useful and not on screen.
    """
    if stack not in UIS:
        return ""
    try:
        return _read(UIS[stack][1] + "-password")
    except (Exception, JThrowable), e:
        _log().warn("no password for %s: %s" % (stack, e))
        return ""


def summary(stack):
    """Everything the popup needs except the password itself."""
    return {
        "stack": stack,
        "label": label(stack),
        "user": username(stack),
        "have": has_credential(stack),
        # Said on the popup, because "why is this one different" is the first
        # question somebody asks after using a gateway button that just worked.
        "why": ("This UI has its own login and cannot be signed in from "
                "outside, so the console opens it and shows you the "
                "credential."),
    }


def _log():
    return system.util.getLogger("ui_creds")
