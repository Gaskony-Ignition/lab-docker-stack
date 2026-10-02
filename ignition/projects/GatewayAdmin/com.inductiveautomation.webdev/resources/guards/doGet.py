def doGet(request, session):
	"""Every guard on the console, as JSON, so what a button is allowed to do
	can be read from a terminal instead of from a screenshot.

	    curl -sk https://console.test/system/webdev/GatewayAdmin/guards

	Read-only and open, like the Sparkplug wire probe beside it: a `can` dict is
	plain words about demo state and carries no secret. The audit that asked for
	these guards asked for this route in the same breath -- the alternative was
	asserting that a button was drawn disabled by looking at a picture of it.

	MUST START AT BYTE 0 WITH THIS def, and nothing -- not even a comment -- may
	precede it, or the route answers HTTP 200 with an empty body and logs
	nothing. See sparkplug/wire/doPost.py for the full account.
	"""
	from java.lang import Throwable as JThrowable

	out = {}

	def safely(name, fn):
		try:
			out[name] = fn()
		except (Exception, JThrowable), exc:
			out[name] = {"error": str(exc)}

	def demos():
		import demo_control
		snap = demo_control.summary()
		return {"busy": snap.get("busy"),
		        "can": snap.get("can"),
		        "tabs": demo_control.tab_state(),
		        "cards": [{"id": d.get("id"), "state": d.get("state"),
		                   "action": d.get("action"), "can": d.get("can")}
		                  for d in snap.get("demos") or []
		                  if not d.get("placeholder")]}

	def eam():
		import gateway_admin
		snap = gateway_admin.summary()
		snap["cards"] = [gateway_admin.card(w) for w in ("hub", 0, 1)]
		return snap

	def storeforward():
		import sf_demo
		return sf_demo.guards()

	def redundancy():
		import redundancy_demo
		snap = redundancy_demo.summary()
		return {"can": snap.get("can"), "notReady": snap.get("notReady"),
		        "banner": snap.get("banner"), "canStop": snap.get("canStop"),
		        "peerConnected": snap.get("peerConnected")}

	safely("demos", demos)
	safely("eam", eam)
	safely("storeforward", storeforward)
	safely("redundancy", redundancy)
	return {"json": out}
