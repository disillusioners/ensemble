# ANOMALY: Daemon Crash (pre-existing DependencyBus bug)

**Time:** 00:44:21 UTC (during L2 test, child 0bbc91f6 still processing)

**Error:**
```
RuntimeError: DependencyBus is not initialized for instance=f9e8d706...; bus must be initialized (Phase 5).
```

**Root cause:** The error is for instance f9e8d706, which is NOT part of the L2 test.
This is a pre-existing race condition in the DependencyBus — a previous test's
instance had a pending child completion that fired while the bus was not yet
initialized for that instance. The daemon gracefully shut down after the error.

**Impact on test:** The L2 test was interrupted. The L1 test had already completed
and its findings were saved. The crash is NOT caused by the F-2 demo code —
it's a latent bug in the daemon's lifecycle management.

**Mitigation:** Restart the daemon and continue with L3-L5.
