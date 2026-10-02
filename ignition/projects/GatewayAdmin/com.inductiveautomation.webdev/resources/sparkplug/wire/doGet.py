def doGet(request, session):
	"""Read-only probe: `curl https://console.test/system/webdev/GatewayAdmin/
	sparkplug/wire` returns what the ring buffer currently holds, so a witness
	that answers nothing is visible from a terminal rather than only from a
	404 nobody explains. `?debug=alarms` / `?debug=events` are diagnostic
	dumps -- see sparkplug_demo.handle_wire_get()'s docstring. No secret in
	here -- GET carries none, same as the edge observer's own contract.

	Same byte-0 rule as doPost.py in this same resource -- see that file.
	"""
	import sparkplug_demo
	return sparkplug_demo.handle_wire_get(request)
