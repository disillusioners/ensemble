# Lessons — promote-handback-halt independent gate (Phase A + B, 2026-11-02/03)

## 1. The `-rEf` message-suffix digest trap (digest-skill candidate)

pytest `-rEf` prints `FAILED <nodeid> - <message>`. The `failed-digest-equality-gate` recipe's prefix-strip (`sed 's/^(FAILED|ERROR) //'`) leaves the message attached, so the SAME node can appear as both net-new and fixed whenever a message embeds a value/timing (observed: shard-01 raw 7/7 diff collapsed to a true 4/4 after correction). Canonical BARE-ID extraction must reduce to the node id alone — longest-known-id-prefix against each shard's own census list is a robust form (`/tmp/phh-t2/redigest.sh`). Recommend the skill body document this.

## 2. `rc=124` ≠ dead shard — the death predicate is "no summary line"

A pytest session can COMPLETE (full summary, valid digest) and then hang in teardown on background service threads until the outer `timeout` fires rc=124. Misclassifying that as a dead/partial shard loses a perfectly good digest (observed on base/integration: session done at 702 s, cap at 1750 s). Conversely a mid-session death (pytest-timeout `os._exit(1)`) produces rc=1 with NO summary — that one is genuinely dead. Check for the summary line before trusting rc.

## 3. Literal-token grep can false-"missing" a pin behind constant indirection

`grep -rn ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER tests/` returned 0 hits ⇒ interim verdict "zero shipped pins" for the daemon kill-switch. The pins existed the whole time, binding via the Python constant `svm.SERVING_GREEN_CLOSER_ENV` (`test_upgrade_serving_green_closer.py:614/:635/:653`). When hunting pin coverage for an env knob, ALSO grep the constant that names it. (Same class as the house "anchor gate greps on CONCEPT tokens, both spellings" rule — this extends it to constant-indirection.)

## 4. A tautological identity-table pack reads green forever

`test_ceremony_wallclock.sh` asserted its own hardcoded literals (90 == 90) and its header claimed it would "FAIL if any future change raises a phase budget" — false until rebound to the shipped lib.sh constants at runtime (now 50/50 with a mutation proof: planted 300→600 ⇒ RED). Audit form for budget/identity packs: does ANY assertion read the shipped value from the code under test? If not, the pack is decoration. Also: its stale "870s/30s-slack" row encoded a fiction the production code's own comments had retracted — pack rows must be re-derived when the code's documented arithmetic changes.

## 5. `proc_run` children die with the daemon; `service_start` survives

The 23:39Z live promote restarted the daemon and the boot sweep killed the corpus's `proc_run` tree (daemon-child cgroup membership; `setsid` does not escape it). `service_start`-managed runs survived the same restart. For any multi-hour gate that must survive operator promotes, run the driver under `service_start` (or accept the resume-safe-manifest recovery path — incremental per-shard persistence made the recovery lossless here).

## 6. Todo-store loss mid-commission (twice)

The daemon OOM-restart child-clobber quirk wiped the todo graph twice in one night. Materialize the graph early, but treat it as a UI convenience — the RESULTS artifacts + PACKS register are the durable state; recreate the graph from them when it vanishes.
