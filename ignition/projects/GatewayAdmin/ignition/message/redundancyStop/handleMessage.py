	# Take THIS gateway down, asked for by its redundant peer.
	#
	# The demonstration is worth far more watched from the survivor than from
	# the casualty: a customer who loses the page has to take the takeover on
	# trust, while one whose page stays up watches the cards change. So the
	# standby asks the active half to go, over the Gateway Network link the two
	# already have, and the active half stops itself.
	#
	# Returns immediately. redundancy_demo.stop_local() only SCHEDULES the
	# restart -- restarting inside this handler would tear down the web server
	# while the reply is still being written, and the caller would report a
	# failure for something that worked. Same trap, same fix, as Styles_Template'
	# breakLink handler.
	#
	# GatewayAdmin is synchronised to the backup by redundancy itself, so this
	# handler exists on both halves without being deployed to either separately.
	import redundancy_demo

	seconds = redundancy_demo.STOP_DELAY_SECONDS
	if payload is not None:
		try:
			seconds = int(payload.get("seconds", seconds))
		except Exception:
			pass

	return redundancy_demo.stop_local(seconds)
