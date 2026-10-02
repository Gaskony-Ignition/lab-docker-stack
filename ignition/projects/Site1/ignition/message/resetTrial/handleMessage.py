	# Reset THIS gateway's trial, asked for by the hub over the Gateway Network.
	#
	# The hub cannot write an edge's trial over HTTP: POST /data/api/v1/trial is
	# requirePermission(WRITE) and authenticated by a session cookie, so it would
	# mean keeping edge gateway credentials on the hub and doing a login flow from
	# Jython. The Gateway Network is already there, already authenticated by the
	# approved connection, and carries no credentials at all -- so the hub asks and
	# the edge does it to itself, in the JVM that owns its own licence manager.
	#
	# Inherited from Styles_Template, so EAM carries it to both edges as part of the
	# flattened Edge project. Harmless on the hub, which simply never calls itself.
	import gw_trial

	minutes = gw_trial.reset_local()
	system.util.getLogger("Trial").info(
		"reset requested over the Gateway Network: %d min" % minutes)
	return minutes
