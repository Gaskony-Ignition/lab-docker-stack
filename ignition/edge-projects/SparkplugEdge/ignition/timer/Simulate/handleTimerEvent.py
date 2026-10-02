	# Run the pump station one step. Once a second, on its own thread.
	#
	# Its own thread (sharedThread false) because a shared timer thread runs every
	# shared timer in turn: AutoScan taking a moment would make
	# the station stutter, and a stutter shows up on the cloud as latency that is
	# not the network's.
	#
	# Everything is in sp_sim; this only keeps an exception from killing the tick.
	# A Java exception is not a Python Exception in Jython, hence JThrowable.
	from java.lang import Throwable as JThrowable

	import sp_sim

	try:
		sp_sim.tick()
	except (Exception, JThrowable), e:
		system.util.getLogger("SparkplugEdge").error("simulate tick failed: %s" % e)
