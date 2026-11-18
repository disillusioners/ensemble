# POK Phase-4 Re-Census — 2026-11-18 (POK sweep wave 3, job 2)

## 0. Provenance & method

- Capture (UTC): **2026-11-18T16:42:50** (server `now()` at first query batch)
- Connection: database=`ensemble_prod` user=`ensemble` host=`10.44.0.2` port=`5432`
- Identity gate: first query of the session was `SELECT current_database(), current_user;` → `ensemble_prod` / `ensemble`; job would have ABORTED on any other database.
- Read-only fence: `PGOPTIONS="-c default_transaction_read_only=on"` on EVERY psql invocation; `SHOW default_transaction_read_only` → `on`, `SHOW transaction_read_only` → `on`. SELECT-only, no locks beyond reads, no DDL, zero DB mutations.
- NO `pok_*` tool calls of any kind (SQL-level census by design). No git operations; this artifact lands untracked in the canonical detached-HEAD checkout.
- Credentials parsed explicitly from the LIVE install env (`/home/nea/agents-ensemble/.env`); the dev-shaped repo `.env` (ensemble_dev) was NOT used. Password never printed.
- **TEXT-timestamp caveat**: `created_at`/`updated_at` are TEXT ISO `+00:00` strings; all windowing is lexicographic with the `T` separator always included (e.g. `created_at >= '2026-11-18T14:36:04'`). Valid for ordering/windows; not a native type.
- Machine-copy discipline: every id/slug below is machine-copied VERBATIM from psql output (unaligned rows) into /tmp scratch files; this file is script-generated (python3 stdlib) from those files. No id/slug was retyped by hand; the only embedded literals are the census timestamp/baseline constants and the standing-rule sentence, both copied byte-exact from the dispatch.
- Scratch dir (intermediates + generator inputs): `/tmp/pok-phase4-recensus-DI0el78p`

## 1. Fresh counts

| status | count |
|---|---|
| active | 273 |
| superseded | 74 |
| archived | 33 |
| **TOTAL** | **380** |

Raw psql output (verbatim):

```text
active|273
archived|33
superseded|74
TOTAL|380
```

Distinct status values observed: exactly {active, superseded, archived} — no unexpected status values (JOB D check).

## 2. Full reconciliation — PM ledger vs observed

Baseline (phase-0 census 2026-11-18T14:36:04Z): active 282 / superseded 60 / archived 11 / total 353.

### 2a. Ledgered actions since census

1. **+13 merge survivors** (project-manager): 3 wave-1 @15:05:51–15:06:50 (5fcb2598, 170d495f, 6db7e0fd) + 10 wave-2 @16:17:10–16:17:35 (1e461c98, 1c7ee827, 54df8107, 58051960, 96c3c897, cf851466, 6757ceb4, c1346dd6, 5ee14ca2, d57f710c).
2. **+1 supersede-refresh survivor** (project-manager): 7e439bcf-1202-4084-855a-ea678e073e37 `group-chat-01-design-landed` @ 2026-11-18T16:18:34.028293+00:00 (superseding 30291831 — linkage machine-verified via `superseded_by_id`).
3. **+1 created-then-archived duplicate** (project-manager): f91dbb87-cc80-4d01-8a1c-44633563a7ad `prompt-assist-flip-on-gate` created 2026-11-18T15:05:51.229322+00:00 → archived 2026-11-18T15:07:04.192835+00:00 — counted in the 22 archives but never reduced census-active.
4. **+4 dispatch-known unledgered inserts**: df41e46a-5da7-4b87-b3fc-ceee15cb150d `tmp-01-triage-fences` (giter @ 2026-11-18T14:51:37.560128+00:00); 65b6ffeb-8bf7-44ef-bb8a-e9993690a5ca `tmp-02-structural-consumers` (giter @ 2026-11-18T14:51:37.574617+00:00); 505b4f14-241f-458f-a65a-38bd55c1e4e3 `wt-04-sidecar-reclaim` (giter @ 2026-11-18T14:51:37.577132+00:00); 8466022d-d394-4089-8334-a44d7fd02d6c `gc-fe-surfaces-span-pin-committed-only` (kb-writer @ 2026-11-18T15:32:56.239024+00:00)
5. **+8 NEW unledgered inserts discovered this census**: see §2c (created list) and §7 item 1 — these are post-wave-2 knowledge capture, NOT sweep machinery.
6. **−22 archives** (wave-1: 9 @15:05:36–15:07:04; wave-2: 13 @16:17:46). Census-active impact = −21 (the other 1 archive(s) hit rows created post-census).
7. **−13 merge flips** active→superseded (same-slug predecessors; `superseded_by_id` links each to its survivor).
8. **−1 refresh flip** active→superseded: 30291831-3763-4e6a-9df4-a4938e0038dc `group-chat-01-design-landed` → 7e439bcf-1202-4084-855a-ea678e073e37
9. **±0 withdrawn-claim ids**: 8f8cee3a / b77128c5 / 1b8e4d80 are ABSENT from `project_operating_knowledge` — verified UNSCOPED (no project filter, id-LIKE and slug-fragment-LIKE all empty). They are not POK store rows (likely claim/evidence ids from the E-6 lane ledger); census impact zero. The related store rows WERE swept: `skillab-manifest-01-no-consumer` (b0611691 superseded → 58051960) and `skillab-manifest-02-claims-withdrawn` (e22957fd archived @16:17:46).

### 2b. Predicted vs observed

| status | predicted | observed | match |
|---|---|---|---|
| active | 282 +13 +1 +4 +8 −21 −13 −1 = **273** | 273 | ✓ |
| superseded | 60 +13 +1 = **74** | 74 | ✓ |
| archived | 11 +22 = **33** | 33 | ✓ |
| total | 353 +27 = **380** | 380 | ✓ |

**Every delta is attributed; zero unexplained rows.** The dispatch hint's `total ≈ 371` under-counted because it omitted the 7e439bcf refresh survivor (+1) and could not foresee the 8 post-wave-2 organic inserts (+8): 353 + 27 = 380.

### 2c. Rows created since census (complete list, machine-copied)

| id | slug | status | created_at | created_by_agent |
|---|---|---|---|---|
| df41e46a-5da7-4b87-b3fc-ceee15cb150d | `tmp-01-triage-fences` | active | 2026-11-18T14:51:37.560128+00:00 | giter |
| 65b6ffeb-8bf7-44ef-bb8a-e9993690a5ca | `tmp-02-structural-consumers` | active | 2026-11-18T14:51:37.574617+00:00 | giter |
| 505b4f14-241f-458f-a65a-38bd55c1e4e3 | `wt-04-sidecar-reclaim` | active | 2026-11-18T14:51:37.577132+00:00 | giter |
| 5fcb2598-673e-4121-b5c0-c2e41d19b3ca | `seal-02-repin-dict-keeps-base-value` | active | 2026-11-18T15:05:51.121626+00:00 | project-manager |
| 170d495f-fb72-4100-a9c0-f23f641e08fa | `prompt-assist-04-freshness-leg-second-race` | active | 2026-11-18T15:05:51.141917+00:00 | project-manager |
| f91dbb87-cc80-4d01-8a1c-44633563a7ad | `prompt-assist-flip-on-gate` | archived | 2026-11-18T15:05:51.229322+00:00 | project-manager |
| 6db7e0fd-a716-4463-8e5f-bfea1a118c87 | `prompt-assent-flip-on-gate` | active | 2026-11-18T15:06:50.439055+00:00 | project-manager |
| 8466022d-d394-4089-8334-a44d7fd02d6c | `gc-fe-surfaces-span-pin-committed-only` | active | 2026-11-18T15:32:56.239024+00:00 | kb-writer |
| f54bb522-0210-4a69-9b3d-0fb200addd9b | `digest-transcription-discipline` | active | 2026-11-18T15:59:47.565482+00:00 | kb-writer |
| a6186646-dcbc-4688-8d6b-9f969e246f52 | `pok-sweep-01-merge-cap-split` | active | 2026-11-18T16:13:38.062041+00:00 | kb-writer |
| 1e461c98-78d0-4674-ae43-b5c9e04d8086 | `mutation-proof-must-target-pin-property` | active | 2026-11-18T16:17:10.207738+00:00 | project-manager |
| 1c7ee827-deda-43c7-9092-1087775a8efc | `shutdown-01-teardown-order` | active | 2026-11-18T16:17:10.210814+00:00 | project-manager |
| 54df8107-8fc5-4d2b-bb6d-718398d64764 | `land-01-merge-shape-guards` | active | 2026-11-18T16:17:10.251982+00:00 | project-manager |
| 58051960-f3e5-493d-9a9e-fb7f94451052 | `skillab-manifest-01-no-consumer` | active | 2026-11-18T16:17:10.260223+00:00 | project-manager |
| 96c3c897-abb0-4e43-b6dd-f25c3fb5c847 | `gc-p4-03-note-drop-turn-end-reminder` | active | 2026-11-18T16:17:10.264288+00:00 | project-manager |
| cf851466-824b-41f4-b069-c4eaad96eddb | `gate-auth-env-302-poison` | active | 2026-11-18T16:17:10.267373+00:00 | project-manager |
| 6757ceb4-489c-4c61-bfe0-4e27db01ea4c | `proxydec-01-openrouter-key-held` | active | 2026-11-18T16:17:10.297725+00:00 | project-manager |
| c1346dd6-5078-48cd-94a1-d16ee0c49a01 | `gc-gate-03-filter-side-pin` | active | 2026-11-18T16:17:10.304000+00:00 | project-manager |
| 5ee14ca2-9452-4c1f-9f54-09519bf44089 | `portfence-01-daemon-port-comes-from-port-env` | active | 2026-11-18T16:17:10.313786+00:00 | project-manager |
| d57f710c-d180-4f3d-b56a-f7841afcd118 | `verify-02-snapshot-l3-red-set` | active | 2026-11-18T16:17:35.323979+00:00 | project-manager |
| 7e439bcf-1202-4084-855a-ea678e073e37 | `group-chat-01-design-landed` | active | 2026-11-18T16:18:34.028293+00:00 | project-manager |
| eb712439-0e00-4ba7-a8c5-3bf6fc86380e | `gcfe-01-shipped-pin-byte-stability` | active | 2026-11-18T16:25:22.071212+00:00 | worker |
| 50eafdf6-b7ca-404c-98a8-b5ba089b2b55 | `fe-bundle-gate-count-scope` | active | 2026-11-18T16:28:00.407200+00:00 | worker |
| ae2374c9-e4b8-4c1a-8ff6-73826e8a8e54 | `fe-bundle-guard-b4b-headroom` | active | 2026-11-18T16:28:10.303114+00:00 | worker |
| 8788f469-9388-4466-842c-b2897b4ace0e | `fe-bundle-warn-set-comparison` | active | 2026-11-18T16:28:28.200773+00:00 | worker |
| 62ac6838-c749-4c90-8dd6-e6bd68afe152 | `fence-01-auth-scrub-blast-radius` | active | 2026-11-18T16:28:32.527576+00:00 | worker |
| eed9948e-23fc-497a-b779-4adac3d9f19e | `counter-01-mutation-ab-shape` | active | 2026-11-18T16:28:42.525903+00:00 | worker |

Count: **27** (raw psql output in scratch `created_since_census.csv`, 27 rows).

### 2c'. Rows archived since census (complete list)

| id | slug | created_at | archived_at (updated_at) | created_by_agent |
|---|---|---|---|---|
| 5b20d029-ead1-4db7-a482-d087417e4d8c | `staged-dirs-v0309-inventory` | 2026-11-15T02:03:36.055817+00:00 | 2026-11-18T15:05:36.298620+00:00 | giter |
| 2625eeb1-a744-47af-9f65-ab76dcb81ad0 | `proxydec-04-tmp-9router-key-file` | 2026-11-09T10:53:47.185960+00:00 | 2026-11-18T15:05:36.304175+00:00 | devops |
| c5a24a7b-1b82-4ff0-a467-8185be8c43fb | `prompt-assist-02-killswitch-boot-raise` | 2026-11-11T06:22:04.261859+00:00 | 2026-11-18T15:06:01.173200+00:00 | worker |
| c367d252-4c33-4a4d-9e94-bf2821a10361 | `ab-39a27f20-strict-superset-fix-pending` | 2026-11-11T05:35:26.024719+00:00 | 2026-11-18T15:06:01.186936+00:00 | skill-keeper |
| 621444b6-49f0-44a7-b7d2-d5b7a9421978 | `skill-fix-defer-sql-census-8e9853e9` | 2026-11-11T07:26:30.263276+00:00 | 2026-11-18T15:06:01.188823+00:00 | skill-keeper |
| ba8fb078-1327-47c7-a524-ee01e2ed3e79 | `seal-partition-repin-sha-orientation` | 2026-11-11T08:04:06.378856+00:00 | 2026-11-18T15:06:01.191474+00:00 | tester |
| 6b8398a2-9cc2-4bc3-8e64-414371818222 | `wt-merge-v02917-stale-index-ref-move` | 2026-11-11T15:26:50.429017+00:00 | 2026-11-18T15:06:01.199999+00:00 | giter |
| f91dbb87-cc80-4d01-8a1c-44633563a7ad | `prompt-assist-flip-on-gate` | 2026-11-18T15:05:51.229322+00:00 | 2026-11-18T15:07:04.192835+00:00 | project-manager |
| 800f13b5-4fe0-43fb-9ac0-aa9e644c3ebc | `prompt-assent-06-freshness-bound-flake` | 2026-11-12T05:09:43.705014+00:00 | 2026-11-18T15:07:04.261960+00:00 | tester |
| 783a1451-b41b-47c9-9e5f-1152480a2109 | `review-02-describe-http-error-call-sites` | 2026-11-18T06:33:11.025553+00:00 | 2026-11-18T16:17:46.331274+00:00 | kb-writer |
| 59ea590b-89ee-42c5-b453-43dcd452b033 | `bind-03-uvicorn-closes-9797-first` | 2026-11-11T13:12:19.440326+00:00 | 2026-11-18T16:17:46.349518+00:00 | leader |
| 65759d88-be5d-4524-9fc4-8804931cce30 | `verify-03-two-detectors-four-guards` | 2026-11-09T11:51:28.645527+00:00 | 2026-11-18T16:17:46.366336+00:00 | kb-writer |
| da205d55-4c98-4bed-97b9-dae06f5e1731 | `sysone-03-openrouter-decision-model-row` | 2026-11-09T11:02:21.801429+00:00 | 2026-11-18T16:17:46.373004+00:00 | kb-writer |
| e22957fd-6b5d-43ff-b486-2dead9206b69 | `skillab-manifest-02-claims-withdrawn` | 2026-11-18T06:47:29.138137+00:00 | 2026-11-18T16:17:46.388216+00:00 | developer |
| 72ef05e8-738d-4d79-afee-b72b034f7b3b | `shutdown-02-port-hold-truth` | 2026-11-11T13:02:30.129458+00:00 | 2026-11-18T16:17:46.391748+00:00 | worker |
| ebe4f878-ac79-4d9e-8ccb-ceebedfa1aa4 | `sonic-03-blast-gate-removed` | 2026-11-14T04:03:47.113895+00:00 | 2026-11-18T16:17:46.402060+00:00 | developer |
| e9d9e684-47c7-472e-a1bb-f6ab43702f47 | `commissioned-proof-list-is-not-ground-truth` | 2026-11-11T08:17:41.995783+00:00 | 2026-11-18T16:17:46.406334+00:00 | kb-writer |
| 26cbdc9d-e2f4-43d9-939d-05e874263a94 | `source-gc-drift-guard-landing-shape` | 2026-11-11T05:23:29.143835+00:00 | 2026-11-18T16:17:46.409763+00:00 | worker |
| 8ebed7dc-69fc-4c73-a6e1-95b3d0903234 | `group-chat-p4-01-carrier-reminder-slot-gap` | 2026-11-16T10:05:53.348503+00:00 | 2026-11-18T16:17:46.418003+00:00 | worker |
| 9e76cac1-2282-4752-b11c-4e51611247b3 | `gc-p3-04-test-preadvance-blindspot` | 2026-11-16T00:59:17.059192+00:00 | 2026-11-18T16:17:46.423385+00:00 | worker |
| d51b6509-1dfa-4f97-b4e3-60ade9192d38 | `prompt-assent-05-auth-env-scrub-family` | 2026-11-12T04:04:22.388662+00:00 | 2026-11-18T16:17:46.434738+00:00 | tester |
| 22e0fb94-e287-4edc-a1ea-36b7aafc298b | `portfence-03-metrics-port-env-knob-works` | 2026-11-11T20:40:36.484171+00:00 | 2026-11-18T16:17:46.442266+00:00 | leader |

Count: **22** (raw in scratch `archived_since_census.csv`, 22 rows).

### 2c''. Rows flipped superseded since census (complete list, with successor link)

| id (predecessor) | slug | superseded_by_id | flipped_at (updated_at) | created_by_agent |
|---|---|---|---|---|
| 9635dc88-1cff-4beb-97b0-37b707fe08e8 | `seal-02-repin-dict-keeps-base-value` | 5fcb2598-673e-4121-b5c0-c2e41d19b3ca | 2026-11-18T15:05:51.122180+00:00 | worker |
| 12d2e95b-1aba-4d42-8086-39bd5ba850db | `prompt-assist-04-freshness-leg-second-race` | 170d495f-fb72-4100-a9c0-f23f641e08fa | 2026-11-18T15:05:51.142249+00:00 | worker |
| 4441123e-8b9c-4168-b78b-525c3653c5a9 | `prompt-assent-flip-on-gate` | 6db7e0fd-a716-4463-8e5f-bfea1a118c87 | 2026-11-18T15:06:50.439476+00:00 | leader |
| b2d5169e-e5bf-4d9f-9a14-d8175becfe6b | `mutation-proof-must-target-pin-property` | 1e461c98-78d0-4674-ae43-b5c9e04d8086 | 2026-11-18T16:17:10.208253+00:00 | tester |
| 41408276-aea2-437b-b2e4-507ea11802ad | `shutdown-01-teardown-order` | 1c7ee827-deda-43c7-9092-1087775a8efc | 2026-11-18T16:17:10.211207+00:00 | worker |
| 7d38955d-869b-4453-a5af-157e5ff43ec6 | `land-01-merge-shape-guards` | 54df8107-8fc5-4d2b-bb6d-718398d64764 | 2026-11-18T16:17:10.252427+00:00 | leader |
| b0611691-7a2e-4756-95ad-882a23131455 | `skillab-manifest-01-no-consumer` | 58051960-f3e5-493d-9a9e-fb7f94451052 | 2026-11-18T16:17:10.260558+00:00 | leader |
| a3020315-8141-4552-aaba-3f351e5a27ee | `gc-p4-03-note-drop-turn-end-reminder` | 96c3c897-abb0-4e43-b6dd-f25c3fb5c847 | 2026-11-18T16:17:10.264628+00:00 | worker |
| c15be838-547b-48b0-9544-c190b2fb7ac5 | `gate-auth-env-302-poison` | cf851466-824b-41f4-b069-c4eaad96eddb | 2026-11-18T16:17:10.267747+00:00 | worker |
| fc96dc4b-b385-4bf3-86c9-4f31d34e9e1d | `proxydec-01-openrouter-key-held` | 6757ceb4-489c-4c61-bfe0-4e27db01ea4c | 2026-11-18T16:17:10.298441+00:00 | devops |
| f8a54e0f-a551-4b85-9e70-1d88d647efc6 | `gc-gate-03-filter-side-pin` | c1346dd6-5078-48cd-94a1-d16ee0c49a01 | 2026-11-18T16:17:10.304604+00:00 | kb-writer |
| 3b219d3e-c497-4382-a3bb-f3a2bb67e48f | `portfence-01-daemon-port-comes-from-port-env` | 5ee14ca2-9452-4c1f-9f54-09519bf44089 | 2026-11-18T16:17:10.314218+00:00 | leader |
| c2915561-aae8-49a1-af92-eb1247c05814 | `verify-02-snapshot-l3-red-set` | d57f710c-d180-4f3d-b56a-f7841afcd118 | 2026-11-18T16:17:35.324375+00:00 | kb-writer |
| 30291831-3763-4e6a-9df4-a4938e0038dc | `group-chat-01-design-landed` | 7e439bcf-1202-4084-855a-ea678e073e37 | 2026-11-18T16:18:34.028933+00:00 | leader |

Count: **14** (raw in scratch `superseded_since_census.csv`, 14 rows) = 13 merge flips + 1 refresh flip.

### 2d. The 62→63 superseded tick — RESOLVED

All currently-superseded rows with `updated_at` in `2026-11-18T14:36:04`..`2026-11-18T16:00:00` (machine-copied, raw):

```text
9635dc88-1cff-4beb-97b0-37b707fe08e8|seal-02-repin-dict-keeps-base-value|5fcb2598-673e-4121-b5c0-c2e41d19b3ca|2026-11-11T07:53:05.573147+00:00|2026-11-18T15:05:51.122180+00:00|worker
12d2e95b-1aba-4d42-8086-39bd5ba850db|prompt-assist-04-freshness-leg-second-race|170d495f-fb72-4100-a9c0-f23f641e08fa|2026-11-11T05:31:38.463354+00:00|2026-11-18T15:05:51.142249+00:00|worker
4441123e-8b9c-4168-b78b-525c3653c5a9|prompt-assent-flip-on-gate|6db7e0fd-a716-4463-8e5f-bfea1a118c87|2026-11-11T07:44:55.975168+00:00|2026-11-18T15:06:50.439476+00:00|leader
```

The window contains **exactly 3 flips — all three are the KNOWN wave-1 merge flips** (9635dc88 @15:05:51.122180 and 12d2e95b @15:05:51.142249 landed 20 ms apart; 4441123e @2026-11-18T15:06:50.439476+00:00 landed 59 s later). No fourth flip and no unexplained row exists in the window; the wave-2 flip batch landed at 16:17:10–16:18:34, outside it.

**Attribution: the observed 62→63 step is the third known wave-1 merge flip (4441123e-8b9c-4168-b78b-525c3653c5a9 `prompt-assent-flip-on-gate` → successor 6db7e0fd-a716-4463-8e5f-bfea1a118c87) at 2026-11-18T15:06:50.439476+00:00 — any pair of lane reads straddling 15:06:50 sees 62 then 63. The tick is FULLY ATTRIBUTED to wave-1 merge execution; no anomaly remains.**

### 2e. Withdrawn-claim ids (8f8cee3a / b77128c5 / 1b8e4d80)

```text
project-scoped query (id LIKE): 0 rows
unscoped query (any project, id LIKE): 0 rows
slug-fragment LIKE over project slugs: 0 rows
```

Verdict: these three ids are NOT rows of `project_operating_knowledge` in any project. The dispatch's "adjusted by the 3 withdrawn rows" clause is a **no-op** on every count. They most plausibly live in the E-6 claims/evidence ledger outside this table; a read-only census cannot distinguish that from a historical hard-delete — flagged for the developer.

## 3. 7-day intake restated (created_at >= 2026-11-11)

| method | count | note |
|---|---|---|
| (i) ACTIVE rows only | **212** | comparable to the phase-0 baseline (216 census-active rows) |
| (ii) ALL statuses | **282** (active 212 / superseded 47 / archived 23) | not comparable to the baseline without the method note |

Sanity: pre-window rows (created < 2026-11-11) = 98; 282 + 98 = 380 = total ✓.

**Method difference, quantified** — the sweep moved original-cohort rows out of `active`: since census, 18 census-cohort rows (created ≥ 2026-11-11, existed at census) were archived and 12 were superseded, while 26 new active rows landed in the cohort. Identity check: 216 − 18 − 12 + 26 = 212 = observed active-only ✓ (net delta vs baseline: -4). The all-statuses number is higher than the baseline because it also counts cohort rows now archived/superseded — including rows archived BEFORE the census that the baseline's active-only method excluded.

Raw psql outputs (verbatim):

```text
active-only: 212
by-status:
active|212
archived|23
superseded|47
all-statuses total: 282
pre-window: 98
```

## 4. Post-sweep intake (created_at >= 2026-11-18T14:36:04)

- Rows created since census: **27**
- Elapsed at capture: 7606s = 0.0880 days
- Rate since census: 306.7 rows/day (all) — 136.3 rows/day excluding the 15 project-manager sweep-machinery writes
- For scale: 7-day daily average = 30.9/day (baseline method, 216/7) or 40.3/day (all-statuses, 282/7). The 2.1-hour window is burst-dominated (merge batch @16:17, worker capture wave @16:25–16:28); it does NOT predict the standing-rule gate — that gate reads the sustained rate through 2026-11-25.

Full row list (machine-copied):

| id | slug | created_at | created_by_agent | status |
|---|---|---|---|---|
| df41e46a-5da7-4b87-b3fc-ceee15cb150d | `tmp-01-triage-fences` | 2026-11-18T14:51:37.560128+00:00 | giter | active |
| 65b6ffeb-8bf7-44ef-bb8a-e9993690a5ca | `tmp-02-structural-consumers` | 2026-11-18T14:51:37.574617+00:00 | giter | active |
| 505b4f14-241f-458f-a65a-38bd55c1e4e3 | `wt-04-sidecar-reclaim` | 2026-11-18T14:51:37.577132+00:00 | giter | active |
| 5fcb2598-673e-4121-b5c0-c2e41d19b3ca | `seal-02-repin-dict-keeps-base-value` | 2026-11-18T15:05:51.121626+00:00 | project-manager | active |
| 170d495f-fb72-4100-a9c0-f23f641e08fa | `prompt-assist-04-freshness-leg-second-race` | 2026-11-18T15:05:51.141917+00:00 | project-manager | active |
| f91dbb87-cc80-4d01-8a1c-44633563a7ad | `prompt-assist-flip-on-gate` | 2026-11-18T15:05:51.229322+00:00 | project-manager | archived |
| 6db7e0fd-a716-4463-8e5f-bfea1a118c87 | `prompt-assent-flip-on-gate` | 2026-11-18T15:06:50.439055+00:00 | project-manager | active |
| 8466022d-d394-4089-8334-a44d7fd02d6c | `gc-fe-surfaces-span-pin-committed-only` | 2026-11-18T15:32:56.239024+00:00 | kb-writer | active |
| f54bb522-0210-4a69-9b3d-0fb200addd9b | `digest-transcription-discipline` | 2026-11-18T15:59:47.565482+00:00 | kb-writer | active |
| a6186646-dcbc-4688-8d6b-9f969e246f52 | `pok-sweep-01-merge-cap-split` | 2026-11-18T16:13:38.062041+00:00 | kb-writer | active |
| 1e461c98-78d0-4674-ae43-b5c9e04d8086 | `mutation-proof-must-target-pin-property` | 2026-11-18T16:17:10.207738+00:00 | project-manager | active |
| 1c7ee827-deda-43c7-9092-1087775a8efc | `shutdown-01-teardown-order` | 2026-11-18T16:17:10.210814+00:00 | project-manager | active |
| 54df8107-8fc5-4d2b-bb6d-718398d64764 | `land-01-merge-shape-guards` | 2026-11-18T16:17:10.251982+00:00 | project-manager | active |
| 58051960-f3e5-493d-9a9e-fb7f94451052 | `skillab-manifest-01-no-consumer` | 2026-11-18T16:17:10.260223+00:00 | project-manager | active |
| 96c3c897-abb0-4e43-b6dd-f25c3fb5c847 | `gc-p4-03-note-drop-turn-end-reminder` | 2026-11-18T16:17:10.264288+00:00 | project-manager | active |
| cf851466-824b-41f4-b069-c4eaad96eddb | `gate-auth-env-302-poison` | 2026-11-18T16:17:10.267373+00:00 | project-manager | active |
| 6757ceb4-489c-4c61-bfe0-4e27db01ea4c | `proxydec-01-openrouter-key-held` | 2026-11-18T16:17:10.297725+00:00 | project-manager | active |
| c1346dd6-5078-48cd-94a1-d16ee0c49a01 | `gc-gate-03-filter-side-pin` | 2026-11-18T16:17:10.304000+00:00 | project-manager | active |
| 5ee14ca2-9452-4c1f-9f54-09519bf44089 | `portfence-01-daemon-port-comes-from-port-env` | 2026-11-18T16:17:10.313786+00:00 | project-manager | active |
| d57f710c-d180-4f3d-b56a-f7841afcd118 | `verify-02-snapshot-l3-red-set` | 2026-11-18T16:17:35.323979+00:00 | project-manager | active |
| 7e439bcf-1202-4084-855a-ea678e073e37 | `group-chat-01-design-landed` | 2026-11-18T16:18:34.028293+00:00 | project-manager | active |
| eb712439-0e00-4ba7-a8c5-3bf6fc86380e | `gcfe-01-shipped-pin-byte-stability` | 2026-11-18T16:25:22.071212+00:00 | worker | active |
| 50eafdf6-b7ca-404c-98a8-b5ba089b2b55 | `fe-bundle-gate-count-scope` | 2026-11-18T16:28:00.407200+00:00 | worker | active |
| ae2374c9-e4b8-4c1a-8ff6-73826e8a8e54 | `fe-bundle-guard-b4b-headroom` | 2026-11-18T16:28:10.303114+00:00 | worker | active |
| 8788f469-9388-4466-842c-b2897b4ace0e | `fe-bundle-warn-set-comparison` | 2026-11-18T16:28:28.200773+00:00 | worker | active |
| 62ac6838-c749-4c90-8dd6-e6bd68afe152 | `fence-01-auth-scrub-blast-radius` | 2026-11-18T16:28:32.527576+00:00 | worker | active |
| eed9948e-23fc-497a-b779-4adac3d9f19e | `counter-01-mutation-ab-shape` | 2026-11-18T16:28:42.525903+00:00 | worker | active |

## 5. Family widths over ACTIVE rows (post-sweep)

Primary grouping per the mission contract: family-ordered slugs match `<family>-<NN>-<tail>`; family = slug with the `-NN-` segment and tail stripped (regex `^(.+?)-\d{2}-` on active rows). Unnumbered slugs (no `-NN-`) are counted separately, not as a family.

Active rows grouped: 175 in 106 families; 98 unnumbered singles.

Top 10 by width:

| family | active members |
|---|---|
| prompt-assent | 4 |
| snapshot-l3 | 4 |
| baseleg | 3 |
| bindfix | 3 |
| discord | 3 |
| disk | 3 |
| forensics | 3 |
| gc-gate | 3 |
| gc-p4 | 3 |
| group-chat | 3 |

**Families with >3 active members (family-cap-refusal evidence, JOB D):** `prompt-assent` = 4, `snapshot-l3` = 4. These widths mean any 4th-member write into these families would hit `pok_family_full` refusal — evidence of potential past refusals, consistent with wave-2's "corpus wide not duplicative" observation.

Secondary grouping (first hyphen token, for comparability with wave-2's `gc`/`stage` counts) — top 8:

| first-token family | active members |
|---|---|
| gc | 17 |
| prompt | 9 |
| skill | 9 |
| stage | 6 |
| gate | 5 |
| rogue | 5 |
| skillab | 5 |
| views | 5 |

## 6. Standing rule (verbatim)

> if intake has not fallen below ~5/day within 7 days of the sweep (by 2026-11-25), bring the POK cap back down DELIBERATELY - that decision returns to the Emperor

## 7. Anomaly-watch summary (JOB D)

1. **Unledgered rows**: 4 dispatch-known (3 giter @14:51:37 + 1 kb-writer @15:32:56) + **8 NEW** post-wave-2 inserts (2 kb-writer: digest-transcription-discipline @15:59:47, pok-sweep-01-merge-cap-split @16:13:38; 6 worker: gcfe-01-shipped-pin-byte-stability @16:25:22, fe-bundle-gate-count-scope @16:28:00, fe-bundle-guard-b4b-headroom @16:28:10, fe-bundle-warn-set-comparison @16:28:28, fence-01-auth-scrub-blast-radius @16:28:32, counter-01-mutation-ab-shape @16:28:42). All are id+slug+created_at+created_by_agent machine-copied in §2c/§4.
2. **62→63 superseded tick: RESOLVED** — no unexplained flip; see §2d.
3. **Withdrawn-claim ids ABSENT from the store** (unscoped-verified) — census no-op; see §2e. Flag: ledger language called them "rows"; they are not POK rows.
4. **Unexpected status values: NONE** — distinct statuses are exactly {active, superseded, archived}.
5. **Family-cap evidence**: `prompt-assent` = 4, `snapshot-l3` = 4 exceed 3 active members (see §5).
6. **Unattributable count mismatches: NONE** — all four counts reconcile to the row (§2b). The only ledger-side imprecision found is the dispatch hint's total ≈371 (real 380), explained in §2b.

---
*Generated 2026-11-18T16:42:50 by the wave-3 job-2 generator (python3 stdlib) from psql scratch outputs in /tmp/pok-phase4-recensus-DI0el78p; artifact sha256 recorded in the dispatch report.*
