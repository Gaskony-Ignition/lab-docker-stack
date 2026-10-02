def doGet(request, session):
	"""GET /system/webdev/Edge/sf/local?since=<epoch ms> -- this edge's OWN audit and
	alarm journal records, the "local" half of the console's evidence table.

	Open (no auth): read-only, nothing secret. Over HTTP so the hub reads Edge 2
	without the Gateway Network. Nothing may precede the `def` (state/doGet.py).
	"""
	from java.lang import Throwable as JThrowable
	try:
		since = request["params"].get("since")
		if isinstance(since, (list, tuple)):
			since = since[0] if since else None
		since = long(since) if since else system.date.toMillis(system.date.addHours(system.date.now(), -2))
		return {"json": sf_local.local(since)}
	except (Exception, JThrowable), e:
		return {"json": {"ok": False, "error": str(e)}}
