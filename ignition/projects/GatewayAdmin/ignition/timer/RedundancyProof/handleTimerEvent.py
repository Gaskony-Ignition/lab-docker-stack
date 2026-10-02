	# Keep the redundancy proof tag in place (redundancy_demo.ensure_proof).
	# Runs on the active half only; the tag reaches the standby by config sync.
	import redundancy_demo
	try:
		redundancy_demo.ensure_proof()
	except Exception, e:
		system.util.getLogger("Redundancy").warn("proof tag sweep failed: %s" % e)
