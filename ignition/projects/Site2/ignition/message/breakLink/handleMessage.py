	# Cut THIS edge's Gateway Network link for a while, asked for by the hub.
	#
	# The hub has no lever of its own here. Banning a peer address at the broker
	# does nothing to an edge that never speaks to the broker, and the one
	# hub-side control that would work -- revoking the incoming connection's
	# approval -- is a security action rather than a network one, and is also how
	# you lose the road you would need to give it back. So the hub asks over the
	# link while it is still up, and the edge cuts and restores itself.
	#
	# This returns immediately: edge_link.cut only writes a deadline, and the
	# edge's own timer disconnects on its next tick. Doing it here would drop the
	# socket this reply travels back on, and the hub would report a failure for
	# something that worked.
	#
	# Inherited from Styles_Template, so EAM carries it to both edges as part of
	# the flattened Edge project. Harmless on the hub, which dials out to nobody.
	import edge_link

	seconds = 60
	if payload is not None:
		seconds = payload.get("seconds", seconds)

	message = edge_link.cut(seconds)
	system.util.getLogger("EdgeLink").info(
		"break requested over the Gateway Network: %s" % message)
	return message
