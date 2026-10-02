def doPost(request, session):
	"""POST /system/webdev/Edge/sf/action {"kind": "audit"|"alarm"|"clear"} -- an
	operator action at this edge, made from the hub's console without the Gateway
	Network. X-WD-Token required (sf-token, scripts/sf-audit-alarms.sh); read from
	the servlet request because header names are case-insensitive on the wire.
	Nothing may precede the `def` (state/doGet.py).
	"""
	from java.lang import Throwable as JThrowable
	response = request.get("servletResponse")
	try:
		presented = request["servletRequest"].getHeader("X-WD-Token")
	except (Exception, JThrowable):
		presented = None
	if not sf_local.token_ok(presented):
		try:
			response.setStatus(403)
		except (Exception, JThrowable):
			pass
		return {"json": {"ok": False, "message": "refused: missing or wrong X-WD-Token"}}
	try:
		body = request.get("postData")
		if isinstance(body, basestring):
			body = system.util.jsonDecode(body)
		return {"json": sf_local.act(body or {})}
	except (Exception, JThrowable), e:
		return {"json": {"ok": False, "message": "action failed: %s" % e}}
