def doOptions(request, session):
	"""Not a method this route answers."""
	try:
		request["servletResponse"].setStatus(405)
	except:
		pass
	return {"json": {"ok": False, "message": "method not allowed"}}
