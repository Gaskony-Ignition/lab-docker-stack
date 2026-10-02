	# Applies file-based project deploys without a Designer or a gateway restart.
	#
	# External edits to data/projects never take effect on their own -- something
	# has to call for a project scan. The only built-in triggers are the Config UI
	# button and a gateway restart, neither of which a deploy script can drive.
	# This timer gives it one: scripts/ign-scan.sh drops a trigger file, this sees
	# it within 5s, requests the scan and removes the file again.
	#
	# Polling a file rather than scanning every tick keeps the gateway idle when
	# nothing has been deployed.
	import os

	TRIGGER = "/usr/local/bin/ignition/data/.scan-trigger"
	log = system.util.getLogger("Ops.AutoScan")

	if not os.path.exists(TRIGGER):
		return

	try:
		os.remove(TRIGGER)
	except OSError:
		# Another tick beat us to it; let that one do the scan.
		return

	try:
		system.project.requestScan()
		log.info("project scan requested by ign-scan.sh")
	except Exception, e:
		log.error("project scan failed: %s" % e)
