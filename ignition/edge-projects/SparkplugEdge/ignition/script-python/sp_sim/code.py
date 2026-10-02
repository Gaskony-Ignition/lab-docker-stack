"""A small pump station that runs on its own. Called once a second by the Simulate timer.

A wet well fills from a varying inflow and one duty pump empties it. In AUTO
the pump starts at setpoint + 32 and stops at setpoint - 38 -- with the default
setpoint of 50 that is 82 % and 12 %. Two consequences are deliberate, because
this demo is about alarms reaching the cloud and they have to happen unprompted:

- the inflow's peak exceeds the pump's capacity, so the level keeps rising after
  the pump starts and crosses Level High (85) once a cycle;
- the stop level sits below Level Low (15), so every drain-down raises it.

Tuned offline before it was written (see sp_site.shape): North cycles every five
minutes between ~12 % and ~93 %, South every three between ~11 % and ~92 %.

What the demo's controls do to it:

    PumpFault true   the pump stops whatever the mode; level rises; Level High
    Mode MANUAL      the pump is held stopped -- same road to Level High
    LevelSetpoint    moves the whole band: raise it and Level High comes sooner
                     and lasts longer, and Level Low stops happening at all

State lives in the tags, not in this module. Gateway scripts restart on every
project scan and every trial reset, and a level held in a module global would
snap back to its default each time.

Values are rounded where they are written. Sparkplug publishes on change, and a
Level carrying fifteen decimal places changes on every tick for nothing -- and
reads badly on every screen that shows it.
"""
import math

from java.lang import System as JSystem
from java.lang import Throwable as JThrowable

import sp_site
import sp_udt

LITRES_PER_PERCENT = 45.0
PUMP_LPS = 60.0
BASE_INFLOW_LPS = 40.0
INFLOW_SWING = 0.8
START_ABOVE = 32.0
STOP_BELOW = 38.0
ENSURE_EVERY_MS = 30000

# Only timing lives here, and losing it costs one tick of dt.
_CLOCK = {"last": 0, "ensured": 0, "missing": False}

READS = ("Level", "LevelSetpoint", "Mode", "PumpFault", "PumpRunning", "Heartbeat")


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def tick():
    """One step. Returns the number of values written (0 = not provisioned)."""
    now = JSystem.currentTimeMillis()
    if now - _CLOCK["ensured"] > ENSURE_EVERY_MS:
        _CLOCK["ensured"] = now
        try:
            sp_udt.ensure_present()
        except (Exception, JThrowable), e:
            sp_site.logger().warn("self-heal provisioning failed: %s" % e)

    qvs = system.tag.readBlocking([sp_site.member_path(n) for n in READS])
    if any(qv.quality.isBad() for qv in qvs):
        if not _CLOCK["missing"]:
            _CLOCK["missing"] = True
            sp_site.logger().warn("station tags not readable at %s -- waiting for provisioning"
                                  % sp_site.instance_path())
        return 0
    _CLOCK["missing"] = False

    level = _float(qvs[0].value, 50.0)
    setpoint = _float(qvs[1].value, 50.0)
    mode = str(qvs[2].value or "AUTO").upper()
    fault = bool(qvs[3].value)
    running = bool(qvs[4].value)
    beat = int(_float(qvs[5].value, 0))

    dt = (now - _CLOCK["last"]) / 1000.0 if _CLOCK["last"] else 1.0
    dt = min(max(dt, 0.2), 5.0)
    _CLOCK["last"] = now

    period, phase, amplitude = sp_site.shape()
    t = now / 1000.0
    inflow = max(0.0, BASE_INFLOW_LPS * (
        1.0 + INFLOW_SWING * amplitude * math.sin(2.0 * math.pi * (t + phase) / period)))

    if fault or mode != "AUTO":
        running = False
    elif level >= setpoint + START_ABOVE:
        running = True
    elif level <= setpoint - STOP_BELOW:
        running = False

    outflow = PUMP_LPS if running else 0.0
    level = min(100.0, max(0.0, level + (inflow - outflow) * dt / LITRES_PER_PERCENT))

    if running:
        pressure = 2.6 + 0.018 * level + 0.15 * math.sin(2.0 * math.pi * t / 7.0)
    else:
        pressure = 0.35 + 0.004 * level

    names = ["Level", "Inflow", "PumpRunning", "DischargePressure", "Heartbeat"]
    values = [round(level, 1), round(inflow, 1), running, round(pressure, 2),
              (beat + 1) % 1000000]
    system.tag.writeBlocking([sp_site.member_path(n) for n in names], values)
    return len(names)
