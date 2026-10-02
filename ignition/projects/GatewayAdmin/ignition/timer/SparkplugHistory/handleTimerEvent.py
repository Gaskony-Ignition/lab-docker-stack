	# Keep tag history switched on for the AlarmDemo station tags on Engine --
	# a SIBLING of SFHistory, not a change to it: two different demos' tags
	# on the one historian, so they get their own sweep rather than sharing
	# one that would need to know about both.
	#
	# Not a one-shot: MQTT Engine RECREATES every AlarmDemo tag from each
	# fresh Sparkplug birth certificate, and a recreated tag comes back with
	# history OFF. Cutting Edge3 off and restoring it (the page's own Cut
	# Edge 3 control) is exactly what produces a new birth -- so the one
	# action this demo is built around is also the one that would silently
	# stop the trend recording, unless this is re-applied.
	#
	# Idempotent and cheap -- it reads each tag's configuration and writes
	# only what is actually missing.
	from java.lang import Throwable as JThrowable
	import sparkplug_demo
	try:
		sparkplug_demo.ensure_history()
	except (Exception, JThrowable), e:
		system.util.getLogger("SparkplugDemo").warn("history sweep failed: %s" % e)
