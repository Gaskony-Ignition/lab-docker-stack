def doGet(request, session):
	"""GET /system/webdev/Edge/sparkplug/state -- the OBSERVATION road.

	The console on the hub reads this edge's own view of its tags, alarms and UDT
	here, to set beside what MQTT Engine received. It is labelled as observation
	on the page and it never carries process data anywhere: process data crosses
	only as Sparkplug B.

	Open (no auth) because it is read-only and holds nothing secret. The document
	is built by sp_observe.state(), which never raises.

	Nothing may precede the `def` in this file -- not a comment, not a blank line,
	not an import. WebDev then answers an empty 200 and logs nothing anywhere
	(toolkit scripting.md). So every import lives inside the handler.
	"""
	from java.lang import Throwable as JThrowable
	try:
		return {"json": sp_observe.state()}
	except (Exception, JThrowable), e:
		return {"json": {"ok": False, "error": str(e)}}
