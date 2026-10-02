def doGet(request, session):
	"""Which seconds of the proof tag this half's historian holds, for
	wd-control's changeover recorder (control/redproof.py).
	`?from=<epoch s>&to=<epoch s>`. Open and read-only, like `guards`.

	Nothing may precede the def (see sparkplug/wire/doPost.py).
	"""
	import redundancy_demo
	return redundancy_demo.handle_proof_get(request)
