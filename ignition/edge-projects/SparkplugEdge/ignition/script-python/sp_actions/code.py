"""The things a LOCAL operator can do at this edge, and the token that guards them over HTTP.

Two callers, one implementation:

- the edge's own Perspective page calls run() directly -- it is already in
  gateway scope and secured by the session, so it needs no token;
- POST /system/webdev/Edge/sparkplug/action is how the hub's console SIMULATES
  that local operator, so the audience can trip a pump "at the edge" from the
  one screen. It carries X-WD-Token, checked against this gateway's secret store.

None of these move process data to the cloud. A trip writes the edge's own tag;
the cloud finds out the way it would in the field, as Sparkplug. The console's
cloud-side controls (acknowledge, write a setpoint) never come through here --
they go through MQTT Engine, which is the whole point.

THE TOKEN. One random value, generated once by scripts/sparkplug-setup.sh and
installed into the `wd` file secret provider on the hub AND every isolated edge
-- never in git, never printed, never in a view. Three traps from CLAUDE.md
("Signing in"), all of which fail as a blank or a `null` rather than an error:
system.secrets raises JAVA exceptions (catch JThrowable as well), PyPlaintext has
getSecretAsString() and no getValue(), and a PyPlaintext owns its buffer (so it
is read in a `with` block and dropped).

Jython 2.7 -- `except X, e`, not `as e`.
"""
from java.lang import Throwable as JThrowable

import sp_observe
import sp_site
import sp_tx
import sp_udt

DEFAULT_USER = "edge-operator"
MODES = ("AUTO", "MANUAL")


def token_ok(presented):
    """Constant-time check of an X-WD-Token against the secret store."""
    if not presented:
        return False
    try:
        with system.secrets.readSecretValue(sp_site.TOKEN_PROVIDER, sp_site.TOKEN_NAME) as plain:
            expected = unicode(plain.getSecretAsString())
    except (Exception, JThrowable), e:
        # The reason, never the value.
        sp_site.logger().warn("action refused: cannot read %s/%s from the secret store (%s)"
                              % (sp_site.TOKEN_PROVIDER, sp_site.TOKEN_NAME, e))
        return False
    if not expected:
        return False
    from java.lang import String as JString
    from java.security import MessageDigest
    return bool(MessageDigest.isEqual(JString(expected).getBytes("UTF-8"),
                                      JString(unicode(presented)).getBytes("UTF-8")))


def _write(member, value):
    path = sp_site.member_path(member)
    quality = system.tag.writeBlocking([path], [value])[0]
    if not quality.isGood():
        raise ValueError("write to %s refused: %s" % (path, quality))


def _trip(body, user):
    _write("PumpFault", True)
    return {"ok": True, "message": "Pump tripped at %s -- Pump Fault is raised at the edge, "
                                   "and the level will now rise." % sp_site.system_name()}


def _reset(body, user):
    _write("PumpFault", False)
    return {"ok": True, "message": "Fault reset at %s -- the pump returns to its mode."
                                   % sp_site.system_name()}


def _set_mode(body, user):
    mode = unicode(body.get("mode") or "").upper()
    if mode not in MODES:
        return {"ok": False, "message": "mode must be one of %s" % ", ".join(MODES)}
    _write("Mode", mode)
    return {"ok": True, "message": "Mode set to %s at %s." % (mode, sp_site.system_name())}


def _set_setpoint(body, user):
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return {"ok": False, "message": "value must be a number"}
    if not 5.0 <= value <= 95.0:
        return {"ok": False, "message": "the setpoint must be between 5 and 95 %"}
    _write("LevelSetpoint", value)
    return {"ok": True, "message": "Level setpoint %.1f %% at %s." % (value, sp_site.system_name())}


def _ack(body, user):
    """Acknowledge at the edge -- one alarm by id, or every unacknowledged one."""
    if body.get("id"):
        ids = [unicode(body.get("id"))]
    else:
        ids = [a["id"] for a in sp_observe.alarms() if a["id"] and not a["acked"]]
    if not ids:
        return {"ok": False, "message": "Nothing unacknowledged at %s." % sp_site.system_name()}
    notes = "acknowledged at the edge (%s)" % sp_site.system_name()
    try:
        system.alarm.acknowledge(ids, notes, user)
    except (Exception, JThrowable), e:
        # The username argument is gateway-scope only; fall back rather than fail.
        sp_site.logger().info("acknowledge with a username failed (%s); retrying without" % e)
        system.alarm.acknowledge(ids, notes)
    return {"ok": True, "message": "%d alarm(s) acknowledged at %s by %s."
                                   % (len(ids), sp_site.system_name(), user), "ids": ids}


def _diverge(body, user):
    variant = unicode(body.get("variant") or "add_member")
    if variant == "base":
        return {"ok": False, "message": "'base' is the shared definition -- use udt_restore"}
    message = sp_udt.apply(variant)
    if body.get("rebirth"):
        message += " " + sp_tx.rebirth(_scope(body))[1]
    return {"ok": True, "message": message, "variant": variant}


def _restore(body, user):
    message = sp_udt.apply("base")
    if body.get("rebirth"):
        message += " " + sp_tx.rebirth(_scope(body))[1]
    return {"ok": True, "message": message, "variant": "base"}


def _version(body, user):
    """Fix 1 (T9): bind this edge's instance to PumpStation (1) or PumpStation_v2 (2)."""
    try:
        version = int(body.get("version"))
    except (TypeError, ValueError):
        version = 0
    if version not in sp_udt.VERSIONS:
        return {"ok": False, "message": "version must be 1 or 2"}
    message = sp_udt.use_version(version)
    if body.get("rebirth"):
        message += " " + sp_tx.rebirth(_scope(body))[1]
    return {"ok": True, "message": message, "typeId": sp_udt.VERSIONS[version][0]}


def _retire(body, user):
    """Delete a definition this edge's instance no longer uses."""
    name = unicode(body.get("name") or "")
    if name not in [n for n, v in sp_udt.VERSIONS.values()]:
        return {"ok": False, "message": "name must be one of %s"
                                        % ", ".join(n for n, v in sp_udt.VERSIONS.values())}
    message = sp_udt.retire(name)
    if body.get("rebirth"):
        message += " " + sp_tx.rebirth(_scope(body))[1]
    return {"ok": True, "message": message}


def _scope(body):
    """"node" (default) or "module" -- see sp_tx.rebirth for why both exist."""
    scope = unicode(body.get("scope") or "node")
    return scope if scope in ("node", "module") else "node"


def _rebirth(body, user):
    ok, message = sp_tx.rebirth(_scope(body))
    return {"ok": ok, "message": message}


def _provision(body, user):
    done = sp_udt.provision()
    if not done:
        return {"ok": True, "message": "Everything was already provisioned at %s."
                                       % sp_site.system_name(), "created": []}
    return {"ok": True, "message": "Provisioned at %s: %s." % (sp_site.system_name(), "; ".join(done)),
            "created": done}


HANDLERS = {
    "trip_fault": _trip,
    "reset_fault": _reset,
    "ack_local": _ack,
    "set_mode": _set_mode,
    "set_setpoint": _set_setpoint,
    "udt_diverge": _diverge,
    "udt_restore": _restore,
    "udt_version": _version,
    "udt_retire": _retire,
    "rebirth": _rebirth,
    "provision": _provision,
}


def run(body, user=None):
    """Perform one action. Always returns {"ok": bool, "message": str, ...}."""
    body = body or {}
    action = unicode(body.get("action") or "")
    handler = HANDLERS.get(action)
    if handler is None:
        return {"ok": False, "message": "unknown action '%s' -- one of: %s"
                                        % (action, ", ".join(sorted(HANDLERS)))}
    try:
        result = handler(body, user or DEFAULT_USER)
    except (Exception, JThrowable), e:
        sp_site.logger().warn("action %s failed: %s" % (action, e))
        return {"ok": False, "message": "%s failed: %s" % (action, e)}
    sp_site.logger().info("action %s by %s: %s" % (action, user or DEFAULT_USER, result.get("message")))
    return result
