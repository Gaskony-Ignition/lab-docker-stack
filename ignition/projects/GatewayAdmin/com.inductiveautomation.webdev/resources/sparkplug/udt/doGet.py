def doGet(request, session):
	"""What the two UDT fixes are judged on (SPARKPLUG.md T9, T10): Engine's
	PumpStation* types, each edge's Engine instance, each edge's own layout and
	its wire timeline. `?since=<epoch ms>` bounds the timeline. Open and
	read-only, like the edge observer's GET.

	Nothing may precede the def (see wire/doPost.py).
	"""
	import sparkplug_demo
	return sparkplug_demo.handle_udt_get(request)
