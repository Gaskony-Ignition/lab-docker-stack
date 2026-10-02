def doPost(request, session):
	"""POST /system/webdev/Edge/sparkplug/action -- a LOCAL operator action, simulated.

	Body {"action": "<name>", ...}; answer {"ok": bool, "message": str}. The actions
	are in sp_actions.HANDLERS. This is how the hub's console stands in for a person
	at the edge (trip the pump, acknowledge here, diverge this edge's UDT). Nothing
	cloud-side comes through here -- acknowledging in the cloud and writing a
	setpoint from the cloud go through MQTT Engine, as Sparkplug.

	X-WD-Token is required and checked against this gateway's `wd` secret store
	(installed by scripts/sparkplug-setup.sh). WebDev's own auth is left off on
	purpose: it is HTTP Basic against a user source whose name differs per
	gateway, and a gateway credential on the hub is exactly what this avoids.

	Read the header from the servlet request, not request["headers"]: header names
	are case-insensitive on the wire and the servlet honours that; a dict does not.
	WebDev pre-decodes a JSON body into postData, but not always -- a string is
	decoded here.

	Nothing may precede the `def` in this file (see state/doGet.py).
	"""
	from java.lang import Throwable as JThrowable
	response = request.get("servletResponse")
	try:
		presented = request["servletRequest"].getHeader("X-WD-Token")
	except (Exception, JThrowable):
		presented = None
	if not sp_actions.token_ok(presented):
		try:
			response.setStatus(403)
		except (Exception, JThrowable):
			pass
		return {"json": {"ok": False, "message": "refused: missing or wrong X-WD-Token"}}
	try:
		body = request.get("postData")
		if isinstance(body, basestring):
			body = system.util.jsonDecode(body)
		result = sp_actions.run(body or {}, (body or {}).get("user"))
	except (Exception, JThrowable), e:
		result = {"ok": False, "message": "action failed: %s" % e}
	if not result.get("ok"):
		try:
			response.setStatus(400)
		except (Exception, JThrowable):
			pass
	return {"json": result}
