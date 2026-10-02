	# Walk the demo signals once a second so there is something real to buffer,
	# forward and trend. MQTT Transmission publishes them automatically -- they
	# sit inside the tag path the transmitter is already configured to watch.
	#
	# No-op on any gateway without the `edge` tag provider, so the same inherited
	# resource is harmless on the hub.
	from java.lang import System as JSystem

	import edge_link
	import edge_stream

	if not edge_stream.available():
		return

	# First, and in its own try. This is what ends a Gateway Network outage, and
	# it has to run even on a tick where the data walk fails -- a cut edge whose
	# link never came back is a far worse failure than a missed sample.
	try:
		edge_link.tick()
	except Exception, e:
		system.util.getLogger("EdgeLink").error("link tick failed: %s" % e)

	try:
		edge_stream.ensure_tags()
		edge_stream.ensure_history()
		edge_stream.tick(JSystem.currentTimeMillis())
	except Exception, e:
		system.util.getLogger("EdgeStream").error("tick failed: %s" % e)
