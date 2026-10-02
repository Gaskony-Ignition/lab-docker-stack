def doGet(request, session):
	"""GET /system/webdev/Edge/sparkplug/action -- what the POST accepts. No secrets."""
	return {"json": {"ok": True,
	                 "message": "POST a JSON body {\"action\": ...} with an X-WD-Token header",
	                 "actions": sorted(sp_actions.HANDLERS.keys()),
	                 "variants": sorted(sp_udt.VARIANTS.keys())}}
