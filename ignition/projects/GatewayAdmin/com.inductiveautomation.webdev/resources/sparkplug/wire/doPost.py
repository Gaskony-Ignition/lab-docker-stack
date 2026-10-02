def doPost(request, session):
	"""wd-control's MQTT listener (control/wire.py) calls this for every
	message on spBv1.0/AlarmDemo/# (+ STATE/# and the reserved notify/#).
	This is the OBSERVATION road: a copy of what crossed the broker, handed
	to the console, never a source of truth for a tag.

	MUST START AT BYTE 0 WITH THIS def, and nothing -- not even a comment or a
	blank line -- may precede it. A WebDev handler file is not run as an
	ordinary module: anything above the def, or a top-level import/helper
	beside it, produces a silent HTTP 200 with an empty body and nothing in
	any log (verified, see this toolkit's own knowledge base). So every import
	and every line of real logic is nested inside the handler, and the actual
	work is one call into an ordinary script module, which carries none of
	this restriction.
	"""
	import sparkplug_demo
	return sparkplug_demo.handle_wire_post(request)
