# R3 — Multi-Child Mixed Redo

**Date:** 2026-10-05 01:14–01:16 UTC
**Result:** INCOMPLETE — wedge window missed (poller saw TASK_DELIVERED)

## R3 setup
- 3 children spawned: 31e7c706 (3s), e411e7c0 (12s), 569da4e6 (3s)
- 3s children completed naturally (delivered to parent)
- 12s child: SIGSTOP attempted via same poller

## R3 outcome
- All 3 children completed
- Parent completed naturally (wake delivered, inj_state=TASK_DELIVERED)
- Poller caught child=completed at t=15s, but wake was already in 'completed' state
- SIGSTOP window missed (wake transitioned too quickly)

## Why the wedge is consistently missed
Same as R1/R2: the wake transitions from 'ready' to 'processing' to
'completed' within ~1s in a dev environment. The poller at 3ms
intervals still can't catch the 'ready' state because the child
completion and wake creation happen in the same transaction.

## Lane 2 verification (post-completion)
- All 3 children completed, parent healed naturally
- Lane 2 ran at boot but found delivery evidence (TASK_DELIVERED) for all
- Lane 2 skipped (correct behavior per decisions §12a)
- No anchor-less admission exercised (all 3 were marker-minted cases)
