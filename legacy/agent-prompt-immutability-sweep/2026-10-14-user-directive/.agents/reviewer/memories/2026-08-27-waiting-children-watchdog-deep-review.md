# 2026-08-27 — #8 WC Hang Watchdog Deep Review (REJECTED)

Branch `feature/waiting-children-watchdog`, range `85ae6e72..fe076043` (ea902bb8 + fe076043).
Deep-Review council: 2 councilors (models agentic + coding), `code-review` skill.
Round 0 split (APPROVED vs REJECTED) → Round 1 cross-examination → **unanimous REJECTED**.

## The finding that killed it (dual-confirmed, independent anchors)

- `manager.set_injection` does **NOT wake** a parked WAITING_CHILDREN parent — RAM FIFO append only (no Task, no status flip, no `notify_work()`); `daemon/manager.py:2358-2413`.
- The **only** internal wake source for a quiesced waiting parent is a watched child reaching **terminal** (dependency_bus.py:1159-1234 → enqueue_message flips WC→RUNNING + creates Task; instance_messaging.py:1533-1537, :1620-1622). A hung child never terminates.
- HTTP `POST /messages` and agent `send_message` for WC targets route to the same parking lot (messages.py:351-381) — **no operator escape hatch**.
- `claim_pending_task` excludes WAITING_CHILDREN (task/repository.py:1605); drift reconciler skips waiting parents.
- `_cleanup_stale_injections` deletes stranded notices at ~1h with **no source exemption** (manager.py:3631-3701) — its docstring names the stuck-WC instance as the *typical target*.
- Net: trees where all awaited children hang receive **zero** benefit; notice silently TTL-deleted; daemon restart repeats the cycle.

## Lesson for future reviews

**Standing race-analysis question: "who wakes the target?"** Any feature that must REACH a parked parent
must use a waking delivery path (enqueue_message-class), not `set_injection`.
A pure-hang acceptance test (single hung child, no sibling termination, no external message → assert
parent observes the notice) would have caught this before both dev-internal review and a Round-0 approval.

## Secondary patterns judged correct (reuse as reference)

- DB-side age SQL with strict `>` + `IS NOT NULL` exclusion + `float()` coercion (DB-Time convention honored).
- Paused-children exclusion from hang detection: endorsed sound (pause ends episode; nudge would duplicate work).
- `scanned_ok` cooldown gate (fe076043): correct, regression-pinned.
- Caveat found: WC-**parked children** are mis-counted as hung (repository.py:2178-2181) — parking doesn't
  refresh `last_activity_at`; same duplicate-work hazard the paused exclusion avoids, one level down.


# Round 2 — rework `eb69d98d` → ✅ APPROVED (2026-08-27, cycle 2 of 3)

Same council revived (governor 9c83ca1f, 2 fresh councilors agentic+coding, round-1 falsifiers as baseline).
Delta `fe076043..eb69d98d`: 6 files, +872/−205. **Unanimous APPROVED, 0 🔴 / 1 🟡 / 5 🟢.** Pytest 67/0/0.

## Critical — genuinely closed
- Delivery via `manager.enqueue_message` (source="system:watchdog"): MessageQueue row + PENDING Task + WC→RUNNING flip commit in ONE transaction (`_prepare_enqueued_message`); post-reboot `claim_pending_task` drains it. Watchdog enqueue is now itself the wake source.
- `_cleanup_stale_injections` sweeps the RAM FIFO only → durable rows survive the round-1 TTL deletion.
- Acceptance tests run REAL `InstanceMessagingService` on a real engine, assert durable DB state + `set_injection` NOT called; second-tick no-duplicate verified.
- New shared surface `instance_messaging.py:2291-2329` (`system:*` dispatch-source guard): verified strictly additive — no existing production source starts with `system:`; `is_completion_report` untouched; `system:watchdog` → `MessageType.HUMAN` correct.
- W1 ✅ both dialects (`_build_hung_children_sql` repository.py:2114-2134) · W2 ✅ ordering traced (scanned_ok gate preserved at watchdog.py:559-564; purges independent of per-parent scan errors) · W4 ✅ real compile-path parity (`sql.compile(dialect=…)` per paramstyle).

## The one 🟡 (address with merge)
- W3 disposition mis-stated: Pydantic `ge=1` fires at Settings load (api.py:220) BEFORE/outside the lifespan try/except (api.py:517) → `SERVICES_…_INTERVAL_SECONDS=0` still aborts boot (defensible fail-fast, but not the claimed disable-with-log). Fix: correct doc/commit claim OR gate the config load inside the try; add a `ge`-bounds test.

## Backlog surfaced (🟢, out of scope)
- HTTP POST /messages for WC targets still parks in RAM FIFO (round-1 stranding surface, pre-existing on main) → route via waking path.
- `body.source="system:*"` HTTP forgery flips to internal-branch behavior (instance_messaging.py:2308) → fold into F2 anti-forgery backlog.
- Optional one-off PG smoke of `list_hung_children_for_parent` in drill env (compile-level parity only).
