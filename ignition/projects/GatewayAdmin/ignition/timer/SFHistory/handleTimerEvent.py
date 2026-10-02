	# Keep tag history switched on for the edge signals the trend draws.
	#
	# Not a one-shot: MQTT Engine recreates its tags from every fresh birth
	# certificate, and a recreated tag comes back with history OFF. Cutting an
	# edge off and restoring it is exactly what produces a new birth, so the one
	# action the demonstration is built around is also the one that would
	# silently stop the trend recording.
	#
	# Idempotent and cheap -- it reads six tag configurations and writes only
	# what is actually missing.
	import sf_demo
	try:
		sf_demo.ensure_history()
	except Exception, e:
		system.util.getLogger("SFDemo").warn("history sweep failed: %s" % e)
