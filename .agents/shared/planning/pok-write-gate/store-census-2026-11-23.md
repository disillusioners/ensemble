# POK Write-Gate — Full-Store Class Census (2026-11-23)

Commission: read-only full-store class census for the POK write-gate lane (project agents-ensemble, 83da04de). Fence honored: NO writes to any live table, NO repairs, NO live-system operations; all reads via SELECT-only against ensemble_prod from the maintenancer seat; `pok_upsert`/`pok_curate` never called. This file is the commission's only write.

## 1. Method

**Detector (verbatim source of truth).** `docs/guidelines/guideline-pok-durable-vs-rolling-state.md` in this worktree — byte-identical to `origin/latest` (sha256 `ce49dddee21fd9c389df15a19c53aeb951e603567fb0c8118119e3bef4091157`). **The guideline contains NO regex literal** (scribe-verified negative finding; its only fenced block is a mermaid diagram). The deterministic detector is its PROSE anchor set:

- L39: "a body containing a version string, a date, a directory listing, or a count is a **snapshot** — rolling state, out of POK"
- L71: "a version, a directory, a date, or a count-that-changes … is a snapshot — convert it to the INVARIANT instead"
- L38 test question: "*would this still be true if today's version number, dates and directory listings were all different?*"
- L40 (ROLLING shapes): "release/staging inventories, 'current version' claims, dated directory listings, per-lane inventory snapshots, counts-that-change"

**Two-layer application.** (1) Lexical first pass — a deliberately over-inclusive PG regex proxy over all 499 bodies: `([0-9]+\.[0-9]+)|([0-9]{4}-[0-9]{2}-[0-9]{2})|/(home|tmp|agents|ensemble)/|([0-9]+ (rows|files|nodes|tests|members|reds|halts|probes|legs|keys|dirs|clones))` (commit-sha tokens are NOT guideline anchors and are excluded; decimals match version shape by design). (2) Semantic adjudication via the L38 test question — a row is ROLLING-STATE only when its operative truth DEPENDS on the anchor; anchors cited as provenance/evidence for a durable rule do not classify it rolling.

**Class precedence (census-local, documented).** ROLLING-STATE > EPISODE/EVENT > INCIDENT > DUPLICATE/REDUNDANT > GENERAL-KNOWLEDGE > OPERATIONAL-HOWTO (default pass). Refinement: dated event NARRATIVES route to EPISODE (→ project history), while RS is reserved for L40's inventory/state shapes (→ KV/release tree) — both classes are out-of-POK; only the routing differs. DUPLICATE here is intra-store only (judgment; exact-body duplicate groups measured = 0), per the amended gate's judging criterion (d). GENERAL-KNOWLEDGE = project-independent craft (→ RAG by nature; no RAG-corpus cross-measurement in this census — that was the separate 2026-11-23 wanderer overlap study).

**Scope note.** The `cutlist-2026-11-23.md` lexical set (~106 rows) was NOT consulted: it exists only as uncommitted runtime state in the canonical detached checkout `/home/nea/ensemble-src`, outside this commission's sanctioned read scope (worktree / origin/latest). Divergence vs that set is expected and documented here instead.

**Enumeration.** Maintenancer seat, `ens_db_postgres_select` only: 4 pages `ORDER BY slug` (150+150+150+49 = 499 = COUNT), aggregate passes, integrity joins. Slug is a perfect key (499 distinct slugs / 499 active rows). Per-row `id` and stamp columns were read by the enumerator and are summarized in §2 (table keys on slug).

## 2. Machine-checked summary (measured 2026-11-23 ~12:40Z, ensemble_prod)

Store shape: 679 total rows = **499 active** + 121 superseded + 59 archived.

| class | n | % |
|---|---|---|
| OPERATIONAL-HOWTO | 396 | 79.4% |
| ROLLING-STATE | 74 | 14.8% |
| EPISODE/EVENT | 21 | 4.2% |
| GENERAL-KNOWLEDGE | 4 | 0.8% |
| INCIDENT | 2 | 0.4% |
| DUPLICATE/REDUNDANT | 2 | 0.4% |

Lexical layer: proxy hits **134/499 (26.9%)** — version-shape 51, ISO-date 59, dir-path 12, count-words 31 (overlapping). Semantic adjudication keeps 74; the 60-row delta = anchor-as-evidence rows that pass the L38 durability test.

Trust/audience/origin: source_tier **agent 499 / curated 0**. audience: both 378, operation 74, implementation 47; `audience_agents` narrowings: 0. pinned 0; disputed 0. domain: verify 287, artifact 61, environment 60, other 53, deploy 37, catalog 1. Creators (top 12): worker 148, kb-writer 131, leader 49, giter 27, project-manager 24, tester 22, developer 20, coder 19, skill-keeper 7, devops 7, maintenancer 6, reviewer 6 (remaining 53 across the long tail).

Access & verification: never-accessed (`access_count=0`) **109 (21.8%)**; `verify_count = 0` on ALL 499 rows — the verify machinery has never fired; `last_verified` non-null on 428 (write-time stamps, not verify events — the counters never moved); `last_accessed_at` max 2026-11-23T12:36:03Z (store is actively read). Top access: plan-body-ledger-agreement 100, plan-predicate-ledger-01-attestation 41, source-gc-drift-guards-branch-tip-sensitive 40, review-01-remeasure-base-red-claims 29, decision-module-01-p1-lane-base-branch 23, pok-scope-01-rolling-state-out 23.

Integrity: exact-body duplicate groups 0; active→active supersede edges 0; pinned far under cap (0 ≤ 2).

Family pressure (family = slug up to first `-NN-`, first-match):
- **OVER the documented cap of 3** (finding F-1): `guard-inv` = 4 members, `prompt-assent` = 4 members — either grandfathered pre-cap rows or an enforcement gap; read-only census, no action taken.
- **AT cap (3 members — next write to these families refuses `pok_family_full`)**: 34 families — baseleg, bindfix, ceremony-clean-checkout, discord, disk, engine-pilot, forensics, gateleg, gc-gate, gc-p4, group-chat, hbk-mut, job-lookup, mutation, p7-item5, packconv, promote, prompt-author, pyinst, quarantine, release, review, seal, settings-registry, skill, skillab, snapshot-l3, testgate, tier2-closure, upgfix, wake-path, wiring, worktree, wtgate.
- All other families (1–2 members): unconstrained.

## 3. Trust tiers — measured inert state

- **CURATED TIER: 0 of 499 rows.** Never minted since store creation. The curated injection band (dedicated band, cap 2 per view) has never rendered a single row — the tier exists in machinery only; curation authority (leader/PM) has never been exercised on this store.
- **PIN BAND: equally inert** — pinned = 0; nothing is always-injected-first.
- **DISPUTED: 0** — suppression machinery unused.
- **VERIFICATION: `verify_count = 0` on all 499** — the verify counter has never incremented once; 428 rows carry `last_verified` stamps acquired at write/curation time. The trust machinery is form-only as measured (this matches the guideline's own §4(ii) L93 audit line).
- **REACH: 21.8% of rows never accessed**; reads concentrate in a small hot set (leader lane's plan rows at 40–100 reads).
- **Gate implication:** the write gate adjudicates the only trust signal that actually flows today — authorship (all 499 rows agent-tier). A curated tier that is never populated cannot serve as a gate input; if the amended guideline wants tier semantics, it must also define who populates the tier and when.

## 4. Full per-row classification (499 rows: slug | class | conf | basis)

| slug | class | conf | basis |
|---|---|---|---|
| ablate-01-base-leg-proof | OH | high | anchor-free ablation method rule |
| abskill-01-canon-file-family4-gap | RS | med | canon-file gap + DB gen ids = point-in-time port task |
| access-predicate-01-narrow-stub-hides | OH | high | durable stub-hides-disjunct trap |
| access-predicate-02-exact-result-set-leg | OH | high | durable e2e exact-set rule |
| ac-comment-token-grep-collision | OH | high | durable AC-vs-comment trap |
| advance-cas-01-fast-path-ownerless-lock | OH | high | durable race-window trap |
| agent-01-skill-injection-flag-scope | OH | med | flag rule durable; 15/11 count is dated evidence |
| agentic-bridge-01-four-agent-model-repin | RS | high | explicit TEMPORARY bridge state, revert-pending |
| anchor-ledger-01-verified-escape-hatch | RS | med | 11-of-27 audit counts = point-in-time |
| anchor-ledger-02-claim-level-anchor-checks | OH | high | anchor audit method rule |
| artifact-gate-01-rollback-source | OH | high | manifest contract gap rule |
| assent-p15-local-imports-are-mutation-pins | OH | high | durable pin rule |
| assertion-vacuity-03-counterfactual | OH | high | durable rule-B |
| assertion-vacuity-03-vocabulary-membership | OH | high | durable token-vocabulary rule |
| attest-01-drift-gate-proceed-on-base | OH | high | durable proceed-on-base rule |
| audit-script-dsn-password-stderr-leak | INCIDENT | high | password-leak hazard, critical-note material |
| auth-lane-scrub-ensemble-auth-env | OH | high | durable scrub-family rule |
| await-sweep-resolution-gaps | OH | high | durable sweep-gap rule |
| baseleg-01-git-archive-no-dotgit | OH | high | durable inversion rule |
| baseleg-01-phase1-base-red-set-protocol | RS | med | base red-set snapshot + flake %; method leg durable |
| baseleg-02-worktree-vs-archive-choice | OH | high | durable venue-choice rule |
| base-rev-pack-run-leaks-listeners | OH | med | old-pack leak trap, predates registry |
| bash-quote-01-single-body-grep-trap | OH | high | durable quoting trap |
| bash-spawn-reaper-silent-stall | OH | high | durable reaper-shape rule |
| bash-spawn-registry-pid-census | OH | high | durable census method |
| behavioral-pin-prod-seam-rule | OH | high | durable seam rule |
| bind-01-uvicorn-bind-after-lifespan | OH | high | durable exit-map contract |
| bind-02-fix-contract-exit-code-preservation | OH | high | durable fix contract |
| bindfix-01-prebind-without-listen | OH | high | durable design fact |
| bindfix-02-budget-for-transient-holders | OH | med | budget rule durable; timing constants dated |
| bindfix-03-blocked-boot-zero-startup | OH | high | durable behavior contract |
| bind-mut-01-scratch-wt-py-resolve | OH | high | durable resolve rule |
| bindrace-01-planning-docs-untracked-wip | RS | high | worktree@sha WIP-only state |
| board-01-ari-only-write-path-leader-blocked | OH | high | durable routing rule |
| boot-auto-cleanup-throttle-gap | OH | med | throttle condition rule; dated example |
| bootlog-01-pre-import-logs-unobservable | OH | high | durable logging hazard |
| bootlog-02-prebind-installs-log-handlers | OH | high | durable ordering fact |
| boot-smoke-01-scratch-cluster-route | OH | high | durable route recipe |
| boot-smoke-02-frozen-bytecode-interpreter | RS | med | interpreter-version pairing rots (3.14 vs 3.13) |
| bootsmoke-scratch-ownership-model | OH | high | durable ownership rule |
| boot-smoke-supersedes-pg-unit-leg | OH | high | durable supersession decision |
| bump-01-preflight-preconditions | OH | high | durable preflight recipe |
| burstabort-01-bind-retry-u-cap | OH | med | cap arithmetic rule; constants dated |
| canonical-01-disk-edit-not-a-git-fact | OH | high | durable identity-copy rule |
| canonical-zero-hbk-code | RS | med | canonical@sha content claim, commit-scoped |
| caplog-count-assert-level-leak | OH | high | durable caplog trap |
| capture-admission-empty-usage-table | OH | high | durable gate semantics |
| capture-admission-gate-chain | OH | high | durable conjunct list |
| carrier-fix-01-bus-api-rename-mocks | OH | high | durable mock-coupling rule |
| census-01-cross-lane-migration-pins | OH | high | durable cross-lane rule |
| census-pin-lane-ownership-form | OH | high | durable pin-scope rule |
| ceremony-clean-checkout-01-tree-identity | OH | high | durable identity assert |
| ceremony-clean-checkout-02-timeout-bounds | OH | high | durable nesting rule |
| ceremony-clean-checkout-03-wt-binding | OH | high | durable binding rule |
| ceremony-clone-04-pth-ordering | OH | high | durable path-order fact |
| ceremony-clone-05-concurrent-daemon-ports | OH | high | durable attribution rule |
| ceremony-count-01-measure-own-pack | OH | low | rule durable; node counts are instance |
| closure-01-char-level-verify | OH | med | rule-dominant; dated example |
| collectmode-01-inactive-retains-status | OH | high | durable host fact |
| collect-only-01-nodeid-count-method | GK | med | generic pytest technique, project-independent |
| counter-01-mutation-ab-shape | OH | high | durable A/B shape |
| coverage-01-bash-steal-atomicity-unpinned | RS | med | dated no-pin coverage state |
| craft-01-exception-swallow-reachability | OH | high | durable reachability method |
| craft-02-no-amend-in-shared-worktree | OH | high | durable amend ban |
| deathclock-01-maintenance-runs-interrupted | OH | high | durable forensics rule |
| decision-client-contract-invariant-185-v187 | RS | med | version-ranged contract claim |
| decisionctl-01-kill-channel | OH | high | durable channel rule |
| decision-kill-switch-liveness-triad | OH | high | durable triad rule |
| decision-module-01-p1-lane-base-branch | RS | high | repo-state pointers (sha + dirty canonical) |
| decision-proxy-typed-answer-shapes | OH | high | durable API contract |
| dev-boot-04-ambient-env-scrub | OH | high | durable inherited-env rule |
| digest-01-per-file-newline-convention | OH | high | durable digest convention |
| digest-02-release-dir-mode-mtime-recipe | OH | high | durable recipe |
| digest-transcription-discipline | OH | high | durable compare rule |
| discord-01-channel-identity-already-present | OH | high | durable payload contract |
| discord-02-user-origin-arms-by-source-type | OH | high | durable predicate fact |
| discord-03-assent-predicate-fix | OH | high | durable predicate seam map |
| disk-01-stage-wt-reclaim-gates | OH | high | durable reclaim gates |
| disk-02-evidence-sidecar-manifest | OH | high | durable sidecar rule |
| disk-03-tag-lineage-unmerged-stage-trees | OH | high | durable lineage rule |
| dispatch-01-worker-barrier-stall | OH | high | durable stall diagnosis |
| dispatch-truncation-recover-queue | OH | high | durable recovery path |
| dotenv-01-scrub-before-source | OH | high | durable order rule |
| dryrun-fd-inheritance-fence | OH | high | durable fd fence |
| dualleg-01-tree-local-venv-isolation | OH | high | durable isolation fact |
| e2e-staged-binary-isolated-boot | OH | high | durable isolation recipe |
| e2s0-01-mirror-claims-and-scratch-db-wall | RS | low | fix-commit + role-capability state; rule leg durable |
| e6-db-pin-skip-guard-fence | OH | high | durable fence rule |
| e6-manifest-pairs-live-db-only | RS | high | store-population counts (live-only pairs) |
| enforcement-dormant-column-fence | RS | low | dormant-column set is schema state |
| engine-boot-fence-env-recipe | OH | high | durable env recipe |
| engine-boot-slo-staleness-rule-absent | RS | high | tip-scoped absence claim |
| engine-boot-slo-warm-band | RS | high | dated measurement band |
| engine-exec-pack-env-fence-01-auth-family | EPISODE | med | CLOSED-at-sha record; 7-var list rides it |
| engine-flipprep-pack-registration-gap | EPISODE | med | CLOSED registration state, pack counts |
| engine-jobs-due-ness-shared-dev-db | OH | high | durable boot-hygiene rule |
| engine-killswitch-env-only-runtime | OH | high | durable env-only contract |
| engine-pilot-01-await-free-window | OH | high | durable code-shape contract |
| engine-pilot-01-diff-audit-pin | RS | med | audit snapshot (BASE_SHA, path counts) |
| engine-pilot-02-ownership-conjunct | OH | high | durable dual-path rule |
| engine-posture-01-use-engine-flags-truthful | OH | high | durable truthful-posture rule |
| engine-shadow-mode-real-exec-flags | OH | high | durable flag recipe |
| engine-warm-gate-datum-extraction | OH | med | datum-source rule durable; band number dated |
| env-01-daemon-user-bus-unreachable | OH | high | durable host fact |
| env-02-collectmode-retains-failed-unit | OH | high | durable host fact |
| env-nonsource-wt-sonic-v0302 | RS | high | named-wt .env state + leak hazard |
| evidence-02-wt-sweep-rescue-durable | EPISODE | high | dated rescue record + archive location |
| evidence-commit-ab-fdc2bb4f-restored | EPISODE | high | A/B resolution + OWED pending state |
| executor-01-transient-cgroup-split | OH | high | durable cgroup fact |
| executor-02-env-scrub-allowlist | OH | high | durable allowlist contract |
| failed-digest-gate-canon-sync-pending | RS | med | landed-yet-shadowing sync state |
| failure-diff-uses-registered-tip | OH | med | rule durable; run counts dated |
| fe-b4b-dynamic-import-chunk-split-cost | OH | med | mechanism rule; byte counts instance |
| fe-bundle-gate-count-scope | OH | high | durable scope rule |
| fe-bundle-guard-b4b-headroom | RS | med | measured-at-commit budget state |
| fe-bundle-warn-set-comparison | OH | high | durable set-identity rule |
| fe-jest-gate-01-worker-pin | OH | high | durable worker pin |
| fe-jest-gate-02-baseline-failures | RS | high | dated failure-set snapshot |
| fence-01-auth-scrub-blast-radius | OH | high | durable blast-radius rule |
| fence-pin-01-function-scoped | OH | high | durable pin-shape rule |
| fence-self-env-ambient-beats-dotenv | OH | high | durable precedence contract |
| fe-operator-surfaces-gear-menu | OH | high | durable UI rule |
| fe-poll-visibility-01-comment-proof-pins | OH | high | durable pin-shape rule |
| fe-report-01-gear-menu-registry | OH | high | durable seam map |
| fidelity-01-diff-verbatim-blocks | OH | high | durable verbatim rule |
| finalize-01-origin-latest-checkout | OH | high | durable add-at rule |
| fixture-listener-01-pkill-selfmatch | OH | high | durable self-match trap |
| fixture-listener-02-subshell-pid-loss | OH | high | durable pid-registry rule |
| flake-01-ab-control-arm-attribution | OH | high | durable rate-compare method |
| flake-01-ab-resolution-staticpool-race | RS | med | quarantine state; mechanism durable |
| forensics-02-debug-log-evicts-evidence | OH | high | durable rotation fact |
| forensics-02-maintenance-runs-death-clock | OH | high | durable clock method |
| forensics-04-registry-truncates-command-text | OH | high | durable truncation fact |
| frozen-smoke-auth-env-pins | OH | high | durable env pins |
| frozen-smoke-live-journal-row | OH | high | durable journal fact |
| g3g4-refusal-message-join-semantics | OH | high | durable text contract |
| g4-mkmutant-shape-drift-on-inplace-mutation | OH | high | durable shape-drift rule |
| g5scan-02-scanner-packs-registration-gap | RS | med | registration-state correction |
| g6-archive-form-degrades-git-ancestry-legs | OH | high | durable vacuity rule |
| g6-freshness-suite-skips-refusal-seam | OH | high | durable by-construction rule |
| gate-auth-env-302-poison | OH | high | durable poison rule |
| gatebase-01-registered-tip-not-spec-commit | OH | high | durable base rule |
| gate-concurrent-mutation-drain-01 | OH | high | durable drain rule |
| gate-crosslane-pins-01-class | OH | high | durable cross-lane rule |
| gate-fence-01-canonical-26-var-scrub | OH | low | 26-name list = current union size (grows) |
| gatefence-01-postgres-db-selfheal-abort | OH | med | fence behavior durable; file count dated |
| gatefence-02-proc-run-sweep-tree-kill | OH | high | durable foreground rule |
| gate-hbk-01-tmp-receipt-contamination | OH | high | durable mtime-bucketing rule |
| gate-langcheck-auth-scrub-false-negative | OH | high | durable scrub-exception rule |
| gateleg-01-env-fence | OH | high | durable fence recipe |

<!-- census-part-1/3-end -->
| gateleg-02-env-fence-auth-family | DUP | med | 21-name count superseded by gateleg-03 union |
| gateleg-03-env-fence-union-scrub | OH | high | current union recipe (list grows — re-derive) |
| gate-mutation-reproof-restore-protocol | OH | high | durable restore proof |
| gate-pipe-tee-exit-code-dash-trap | OH | high | durable dash trap |
| gatesafe-01-inplace-plants-contaminate | OH | high | durable contamination rule |
| gate-whole-tree-slow-skill-sink-test | RS | med | timing state by interpreter version |
| gc-boot-01-addressed-rows-never-derive | OH | high | durable derivation rule |
| gcfe-01-shipped-pin-byte-stability | OH | high | durable no-regression proxy rule |
| gc-fe-surfaces-span-pin-committed-only | OH | high | durable committed-span rule |
| gc-gate-01-fence-family-exclusions | RS | med | exclusion list tied to current fences |
| gc-gate-02-floor-scope-composition-flakes | OH | high | durable floor rule |
| gc-gate-03-filter-side-pin | OH | high | durable over-inclusion pin |
| gc-gate-fence-family-and-xunit2-digest-trap | OH | high | durable isolation rule |
| gc-p3-boot-pin-file-node-count | RS | high | node-count pin state at named shas |
| gc-p3-swallow-advance-class-sweep | OH | high | durable class-sweep rule |
| gc-p3-testp11-class-identity | RS | med | verification snapshot at named sha |
| gc-p4-01-nudge-gate-executed-graph-green | RS | med | validation state at named sha |
| gc-p4-02-executed-graph-probe-recipe | OH | high | durable probe recipe |
| gc-p4-03-note-drop-turn-end-reminder | OH | high | durable reminder rule |
| gc-p4-cle-line-tripwire-repin | OH | high | durable re-pin rule |
| gc-p4-junitxml-is-the-red-source | OH | high | durable red-source rule |
| gc-p5-no-packs-row-added | RS | high | branch-range byte-state |
| gc-p5-room-create-cap-race | OH | high | durable race analysis |
| gc-p5-room-gate-node-baseline | RS | high | node-count baseline |
| git-01-canonical-detached-diff-trap | OH | high | never-commit-there rule; sha is evidence |
| git-01-commit-msg-before-dashdash | OH | high | durable git trap |
| git-sha-transcription-discipline | OH | high | durable sha rule |
| git-update-ref-worktree-index-stale | OH | high | durable stale-index rule |
| graph-seam-pins-need-executed-composition | OH | high | durable composition rule |
| group-chat-01-design-landed | RS | high | landed/LIVE-since state at named sha |
| group-chat-02-p1-committed-docs-stale | RS | high | uncommitted-docs merge-pending state |
| group-chat-03-p2-typed-collision-fix | RS | high | unmerged-worktree state |
| group-chat-p4-nudge-latch-manager-ram | OH | high | durable latch semantics |
| guard-01-laundering-scope-entire-text | OH | high | durable scoping rule |
| guard-inv-01-at-least-as-strong-not-superset | OH | high | durable invariant rule |
| guard-inv-02-bash-refuses-fixtures | OH | high | durable authoring rule |
| guard-inv-03-heredoc-body-not-in-scan-text | OH | high | durable scan rule |
| guard-inv-04-ssh-forwards-stdin-is-executor | OH | high | durable executor rule |
| guard-refuses-readonly-live-observation | OH | high | durable remedy rule |
| guidelines-01-empty-basedir-cwd-injection | OH | med | trap durable; adjudication pending |
| hbk-attribution-02-isolated-tmpdir-probe | OH | high | durable probe rule |
| hbk-gate-01-base-extraction-no-venv | OH | high | durable cascade rule |
| hbk-gate-02-preflight-census-verdict | EPISODE | med | dated dual-side verdict record |
| hbk-handback-file-classes | RS | med | taxonomy durable; live-file count dated |
| hbk-lane-tmp-scratch-inventory | RS | high | explicit count-only inventory |
| hbk-mint-02-lazy-mint-fix | EPISODE | med | FIXED-at-sha record + pin pointer |
| hbk-mint-03-sidechannel-contract | OH | high | durable contract |
| hbk-mut-01-mutation-result-map | RS | med | pack-state mutation counts |
| hbk-mut-02-label-swap-and-l9-collateral | EPISODE | med | dev-brief labeling discrepancy record |
| hbk-mut-03-scratch-pattern-owner | OH | high | durable attribution rule |
| hbk-receipt-01-external-churn | EPISODE | med | dated churn event attribution |
| hbk-receipts-01-foreign-churn-confound | OH | high | durable bucketing method |
| hermetic-01-capture-sink-cwd-fallback | OH | high | durable fence rule |
| identity-01-mainpid-vs-binder-pid | OH | high | identity rule durable; pid example |
| injection-01-fifo-lifetime-teardown | OH | high | durable teardown map |
| injection-02-no-revive-hard-terminal | OH | high | durable drain rule |
| job-lookup-01-canonical-id-seam | OH | high | durable id seam |
| job-lookup-02-fail-closed-typo-pins | OH | high | durable fail-closed rule |
| job-lookup-03-scope-disjointness | OH | high | durable scope map |
| job-receipt-01-status-derivation | OH | high | durable derivation map |
| killfoot-01-broad-pkill-pattern-hits-live | OH | high | durable kill-scope rule |
| kv-setkv-replaces-whole-value | OH | high | durable KV contract |
| land-01-merge-shape-guards | OH | high | durable landing rule |
| land-02-stage-order-push-tag-stage | OH | high | durable order rule |
| lane-base-repoint-recipe-tum-p1 | EPISODE | med | dated lane-repoint instance; recipe embedded |
| language-check-order-scoped-isolation | OH | high | durable isolation rule |
| launcher-01-respawn-is-internal-not-systemd | OH | high | durable supervision fact |
| launcher-01-sha256-pin-chain | OH | high | durable pin chain |
| launcher-stats-restart-forensics | OH | high | durable forensics rule |
| ledger-gap-routing-post-release | OH | high | durable routing rule |
| live-state-json-volatile-fence-baseline | OH | high | delta-not-equality rule |
| live-unit-01-system-level-not-user-manager | OH | high | durable manager fact |
| live-views-filename-allowlist | OH | high | durable gate contract |
| log-truth-pin-observed-flag-mutation | OH | high | durable mutation rule |
| maintenance-dst-fold-warning-empty | OH | high | durable pin shape |
| maintenance-dst-warning-log-surface | OH | high | durable surface rule |
| maintenance-p2-01-phase2-landed-state | RS | high | landed + pins-still-owned state |
| maintenance-report-artifact-convention | OH | high | durable artifact convention |
| maintenance-results-root-basis | OH | high | durable root rule |
| maintenancer-seat-read-only | OH | high | durable seat contract |
| maintenance-uniformity-smoke-band | OH | med | band rule durable; observed counts dated |
| maint-starve-pack-hermetic-no-data-fixture | RS | med | pack-shape state (fixture absence) |
| manifest-map-key-resolution-anchors | OH | high | durable resolution anchors |
| manifest-map-key-resolution-convention | DUP | med | same convention restated (slimmer twin) |
| memoir-census-pin-biting-mutations | OH | high | durable pin-proof rule |
| memoir-census-pin-lane-scoped | OH | high | durable lane-scope rule |
| merge-01-ram-guard-containment-env-order | OH | high | durable order contract |
| merged-shape-guard-verification-method | OH | high | durable three-arm method |
| merge-keep-both-union-proof-01 | GK | med | generic merge-proof technique |
| metrics-lane-01-adjudication-red-preexisting | RS | med | red-at-every-base state |
| metrics-lane-02-record-reduces-not-closes | OH | high | durable adjudication rule |
| model-vocabulary-01-canonical-aliases | OH | high | Emperor doctrine, durable |
| multifile-leg-form-shards-not-perfile | OH | high | durable shard rule |
| mutation-01-guards-need-counter-mutation | GK | med | generic counter-mutation methodology |
| mutation-02-worktree-scratch-venue | OH | high | durable venue recipe |
| mutation-03-git-restore-byte-exact-proof | OH | high | durable restore proof |
| mutation-count-01-boundary-strip-masking | OH | high | durable masking rule |
| mutation-proof-01-t51-idle-gate-anchor | OH | med | lane-specific mutation map |
| mutation-proof-must-target-pin-property | GK | med | generic proof-spec rule |
| mut-lane-artifact-naming | OH | high | durable naming convention |
| none-vs-zeropulse-signal-shape | OH | high | durable shape rule |
| offloop-audit-01-evidence-base-caveats | EPISODE | med | audit-revision record with epoch counts |
| optanchor-01-runner-option-semantics | OH | high | durable arity rule |
| optanchor-02-value-option-anchors-on-value | OH | high | durable anchoring rule |
| orphan-watcher-sweep-birth-dead | OH | high | structural defect; version note |
| ownership-map-diff-audit-flag | OH | high | durable adjudication rule |
| p11-drift-pin-01-span-vacuous | OH | high | durable vacuity rule |
| p11-drift-pin-02-pin-paths | OH | med | location semi-durable; node counts dated |
| p1-ac6-anchor-ledger-delta | RS | med | ledger-vs-body count state |
| p7-item5-01-kwarg-drift-processing-seam | OH | high | durable seam map |
| p7-item5-02-queue-routing-is-mock-await | OH | high | durable attribution |
| p7-item5-03-offender-set-group-count | RS | med | dated AST census counts |
| pack-01-summary-regex-must-match-printf | OH | high | durable grep-shape rule |
| pack-auth-fence-sibling-idiom | OH | high | durable idiom |
| pack-ci-top-level-unit-ungated | RS | med | coverage-gap state (orphans counted) |
| packconv-01-extend-file-owes-no-new-row | OH | high | durable house rule |
| packconv-02-cite-packs-lines-at-base-tree | OH | high | durable cite-against-tree rule |
| packconv-03-unregistered-pack-no-coverage | OH | high | durable preflight rule |
| packdrift-01-detached-head-pin-false-red | OH | high | durable false-red rule |
| pack-registry-row-authoritative | OH | high | durable row-wins rule |
| packs-01-conflict-keep-both-newest-first | OH | high | durable conflict rule |
| pack-shape-01-scrubbed-vs-pg-pinned | OH | high | durable population split |
| packs-registration-01-canonical-stale | OH | high | durable read-source rule |
| packs-registration-02-house-grep-audit | OH | high | durable audit grep |
| packsrow-01-inv-reconcile | OH | high | durable row-merge rule |
| packs-row-count-drift-after-style-pass | OH | high | durable reconcile rule |
| packs-row-scrub-vs-gateleg-union | OH | high | durable union-wins rule |
| pack-trap-01-signal-no-exit | OH | high | durable trap order |
| parallel-01-write-then-verify | OH | high | durable serialization rule |
| pg-ensure-cols-is-one-txn | OH | high | durable txn fact |
| pg-executemany-rowcount-minus-one | OH | high | durable assert-table rule |
| pg-schema-fence-connect-args-not-listener | OH | high | durable schema fence |
| pid-numeric-band-not-identity | OH | high | durable identity rule |
| plan-anchor-paths-need-grep-verify | OH | high | durable grep-first rule |
| plan-anchor-supersession-post-absorb | OH | high | durable routing note |
| plan-body-ledger-agreement | OH | high | durable body-wins rule |
| planner-todo-store-ram-only | OH | high | durable rebuild rule |
| plan-predicate-ledger-01-attestation | OH | high | durable ledger shape |
| pok-family-01-cap-3-per-family-prefix | OH | high | machinery contract (see finding F-1) |
| pok-family-key-01-multi-token | OH | high | durable key rule |
| pok-family-key-cross-family-leak | OH | high | durable defect note |
| pok-scope-01-rolling-state-out | OH | high | doctrine row |
| pok-sweep-01-merge-cap-split | OH | high | durable split rule |
| porcelain-midwrite-false-positive | OH | high | durable transient rule |
| portfence-01-daemon-port-comes-from-port-env | OH | high | durable port resolution |
| portfence-02-daemon-hands-port-to-children | OH | high | durable inheritance fact |
| postgres-marked-suites-collect-zero | OH | high | durable marker fact |
| predicate-discriminator-01-durable-key | OH | high | durable key rule |
| priv-category-rotates-mutation-matrix | OH | high | durable rotation rule |
| proc-01-no-durable-registry | OH | high | durable registry fact |

<!-- census-part-2/3-end -->
| promote-01-false-halt-self-commits | OH | med | mechanism rule durable; halt-count instance |
| promote-03-handback-rc3-pending | OH | med | rc3-equals-pending rule durable; halt-set dated |
| promote-04-rollback-target-symlink | OH | high | durable rollback rule; regression example dated |
| promote-forensics-board-notes-quotes | OH | high | durable evidence-location rule |
| promote-forensics-journal-state-json | OH | high | durable journal-map rule |
| promote-forensics-lane-reach-limits | OH | high | durable lane-reach map |
| promote-halt-gate-01-ac-coverage-split | RS | med | AC-to-pack mapping state |
| prompt-assent-01-gate-owner-and-evidence | OH | med | owner/seam map semi-durable; lane-dated |
| prompt-assent-02-category-registration-seam | OH | med | registration recipe; registry counts rot |
| prompt-assent-03-paths-use-assent-not-assist | RS | med | in-flight lane path-naming state |
| prompt-assent-04-dm-shield-tool-gate | OH | high | durable gate-aim rule |
| prompt-assent-flip-on-gate | OH | med | arming procedure durable; probe pending |
| prompt-assent-pg-server-defaults-gap | OH | high | durable schema contract |
| prompt-assist-01-discord-first-mint | OH | high | durable mint contract |
| prompt-assist-04-freshness-leg-second-race | OH | high | durable never-fix-in-lane rule |
| prompt-assist-tier2-gate-netnew-2 | EPISODE | med | dated tier-2 gate verdict |
| prompt-author-01-cardinal-tier-rule-cap | OH | high | durable prompt-shape rule |
| prompt-author-02-org-chart-fallback-phrase | OH | high | durable wording rule |
| prompt-author-03-pd4a-citation-by-section | OH | high | durable citation rule |
| prompt-baseline-01-edit-stales-row | OH | high | durable staleness rule |
| provider-01-402-wake-kill | INCIDENT | high | dated outage event, critical-note material |
| proxydec-01-openrouter-key-held | RS | med | pending-config state (missing model row) |
| proxydec-02-systemone-canonical-wire | OH | high | durable wire contract |
| proxy-decision-lane-always-on-no-flag | OH | high | durable deployment semantics |
| proxy-decision-provider-model-bound | OH | high | durable binding rule |
| proxy-git-topology-local-master-lags | RS | med | repo/deployed-version state |
| py-fromimport-staticmethod-importerror | OH | high | durable import trap |
| pyinst-01-frozen-binary-audit-route | OH | high | durable audit route |
| pyinst-02-frozen-module-extract-execute | OH | high | durable extract recipe |
| pyinst-03-release-binary-interpreter-version | RS | high | interpreter-version pairing rots |
| pytest-01-sessionstate-annotations-mock | OH | high | durable mock-annotation trap |
| pytestslow-01-ceiling-ref-boundary | RS | med | timeout-boundary timing state |
| pytestsrc-01-edit-during-run | OH | high | durable settle rule |
| quarantine-01-chart-seal-row-is-stale | RS | med | base-scoped seal verdict |
| quarantine-01-row-names-all-reds | OH | high | durable row-completeness rule |
| quarantine-02-family-rows-need-node-mapping | OH | high | durable family-mapping rule |
| ram-guard-01-ambient-bash-stamp-inherited | OH | high | durable lane-aware rule |
| raw-delete-silent-zero-rows | OH | high | durable rowcount-guard rule |
| redclaim-01-rederive-mutation-provenance | OH | high | durable review rule |
| regate-01-concurrent-mutation-worktree | OH | high | durable collision rule |
| regate-02-orphan-sleep-reap-filter | OH | high | durable re-snap rule |
| regate-b5-01-r3-trio-verdict | EPISODE | med | dated re-gate verdict |
| regate-b5-02-boot-smoke-family-list | EPISODE | med | dated pre-existing-red attribution |
| re-gate-rollback-target-count-34-1 | RS | med | declared-vs-observed count drift |
| release-01-bump-tag-then-stage-order | OH | high | durable release order |
| release-02-fetch-tag-clobber-two-locals | RS | med | host tag-divergence state |
| release-03-never-recut-pushed-tag | OH | high | durable tag rule |
| release-journal-pack-01-run-fence | RS | med | stale-cap timing state |
| release-line-publish-01-ff-only-pattern | OH | high | durable publish pattern |
| release-line-publish-02-ceremony-tag-push | OH | high | durable ceremony exception |
| retention-01-evict-sort-key | OH | high | durable retention rule |
| review-01-remeasure-base-red-claims | OH | high | durable re-measure rule |
| review-01-shared-helper-blast-radius | OH | high | durable blast-radius rule |
| review-02-gate-vs-notify-coverage | OH | high | durable gate/notify split |
| review-pack-01-clean-checkout | OH | high | durable clean-checkout rule |
| rogue-01-write-quant-method | OH | high | durable quant method |
| rogue-cleanup-rider-recorded-addendum | OH | high | durable addendum rule |
| rogue-fence-ambient-allow-var | RS | med | allow-var tiering; guard version-dated |
| rogue-p2-evidence-locations | RS | med | lane-state + archive location |
| rollback-target-c7-anchor-promote-1266 | RS | med | line-anchor drift state |
| scheduling-01-row-delivery | OH | high | durable row-delivery map |
| scheduling-01-self-alarm-reuse-instance | OH | high | durable reuse-instance rule |
| scripts-pin-01-floor-70a0960e5 | RS | high | floor-value + pin-containment state |
| seal-01-restamp-requires-unseal-row | OH | high | durable re-stamp rule |
| seal-02-repin-dict-keeps-base-value | OH | high | durable re-pin dict rule |
| seal-03-repin-mutation-proof-triple | OH | high | durable triple proof |
| sealcheck-01-readonly-git-objects-check | OH | high | durable read-only check |
| sealmap-01-location-and-in-play-files | RS | med | seal-map location/count state |
| sealmap-03-older-base-red-not-attributable | RS | med | base-scoped adjudication state |
| services-unit-pre-existing-reds-2026-11-11 | RS | high | dated red-set baseline |
| settings-registry-01-config-path-gate | OH | high | durable config-path rule |
| settings-registry-02-mutation-loop-unfenced | OH | high | durable loop-gap rule |
| settings-registry-03-docstring-claim-scope | OH | high | durable claim-scope rule |
| shared-meta-kv-01-list-value-encoding | OH | high | durable encoding contract |
| shared-worktree-amend-trap | OH | high | durable amend ban |
| shell-03-s2-canary-verb-parse | OH | high | durable parse rule |
| shutdown-01-teardown-order | OH | high | durable teardown order |
| shutdown-03-lifespan-budgets | OH | med | teardown shape durable; constants dated |
| skill-01-off-by-absence-restart-asymmetry | OH | high | durable switch semantics |
| skill-02-evolution-switch-precedence | OH | high | durable precedence chain |
| skill-03-refusal-observability-split | OH | high | durable two-path split |
| skillab-01-zero-traffic-ab-stall | OH | high | durable stall mechanism |
| skillab-02-keeper-rule-zero-traffic-fix | OH | high | durable keeper rule |
| skillab-03-comparison-counter-semantics | OH | high | durable counter semantics |
| skillab-dev-zero-rows-ro-grant-gap | RS | med | env/grant population state |
| skillab-manifest-01-no-consumer | OH | high | durable no-consumer fact |
| skill-ab-resolve-01-guard-parity-all-paths | OH | high | durable guard-parity map |
| skill-ab-resolve-02-svc-sanctioned-unpinned | RS | med | pin-coverage state |
| skill-clone-ceremony-symlink-venv | OH | high | durable ceremony rule |
| skill-e6-02-sweep-crowns-newer-gen | OH | high | durable sweep semantics |
| skill-e6-04-a4-gauge-grew-not-collapsed | RS | high | gauge-count divergence state |
| skillevo-01-content-pipeline-no-patch-mode | OH | high | durable pipeline fact |
| skill-evolution-01-gen-allocation | OH | high | durable allocation rule |
| skill-evolution-02-winner-stamping | OH | high | durable winner rule |
| skill-lane-flip-verify-restart | OH | high | durable verify rule |
| skill-reg-67file-batch-sizing | RS | med | timing/batch-count state |
| skill-reg-67file-known-red-baseline | RS | high | dated red baseline |
| snapshot-l3-01-malformed-json-empty-collapse | RS | med | base-scoped behavior claim |
| snapshot-l3-02-base-red-set-one | RS | high | dated baseline red-set |
| snapshot-l3-04-drift-pin-identity-hazard | OH | high | durable import-identity rule |
| sonic-01-git-grant-boundary | OH | high | durable grant boundary |
| sonic-02-complexity-routing-doctrine | OH | med | doctrine durable; staging state dated |
| sonic-bump-version-selfcommits | OH | high | durable bump behavior |
| sonic-stage-fresh-wt-fe-prebuild | OH | high | durable prebuild rule |
| source-gc-02-cross-lane-carve-out-cure | OH | high | durable carve-out rule |
| source-gc-drift-guards-branch-tip-sensitive | OH | high | durable drift-guard rule |
| sql-01-instances-updated-at-varchar | OH | high | durable type/format rule |
| ssp-scratch-prefix-shared-parallel-lanes | RS | med | shared-prefix hazard; lane counts dated |
| stage-01-live-mode-guards | OH | high | durable stage guards |
| stage-02-boot-smoke-copy | OH | high | durable smoke recipe |
| stage-ceremony-census-proofs | RS | med | ceremony-count state (baseline 181) |
| staged-release-01-dir-not-durable-keep3 | OH | high | durable eviction rule; dated example |
| stage-lane-evidence-capture | OH | high | durable capture rule |
| stage-recipe-fe-prebuild-required | OH | high | durable FE prebuild rule |
| stagerelease-01-binary-sql-toc-census-route | OH | high | durable census route |
| stagerelease-02-stage-vs-promote-origin | OH | high | durable origin split |
| stage-release-gate-sequence | OH | med | sequence durable; baseline count dated |
| stage-scripts-advance-journal-row | OH | high | durable trace-fact rule |
| stage-sh-live-requires-live-arming-var | OH | high | durable arming rule |
| stage-unbumped-floor-pair-ok | OH | med | gate shape durable; floor values dated |
| stageverify-01-receipt-stdout-only | OH | high | durable receipt rule |
| stage-wt-no-env-dev-pins | OH | high | durable no-env pins |
| staging-01-scratch-boot-db-ambient-allow | OH | high | durable scratch rule |
| stamp-pg-pins-shape-trap | OH | high | deterministic shape; sha example |
| sudo-nopasswd-by-design | OH | high | Emperor doctrine |
| supervise-01-launcher-loop-is-the-respawner | OH | high | respawner rule; restart-count example |
| sweep-01-predicate-would-have-reaped-rogue | OH | high | durable predicate rule |
| sysone-01-9router-route | OH | high | durable route fact |
| sysone-02-openrouter-route | OH | high | durable route fact |
| systemd-01-collectmode-retention | OH | high | durable systemd fact |
| tag-read-01-lsremote-yields-tagobj | OH | high | peel rule durable; host-tag example |
| teigate-01-auth-cluster-identical-both-sides | EPISODE | med | dated cluster verdict |
| terminate-budget-01-fleet-3-per-24h | OH | high | durable budget rule |
| test-01-d2-watchdog-hold-privleg-inert | OH | high | durable inert-by-design fact |
| testenv-01-unit-shard-hard-abort | OH | high | durable fence behavior |
| testgate-01-explicit-paths | OH | high | durable explicit-paths rule |
| testgate-02-standalone-packs | OH | high | durable invocation rule |
| testgate-03-double-drift-hides-branch | OH | high | durable delegation rule |
| test-pack-01-embed-consumers-drift-pin | RS | med | branch-scoped drift-pin state |
| tick-budget-01-not-a-wall-clock-bound | OH | high | durable budget shape |
| tier1-declared-set-widening-pass | RS | med | sweep-count state |
| tier2-closure-01-worktree-exit78-fence | OH | high | durable worktree fence |
| tier2-closure-02-nodeid-space-truncation | OH | high | durable id-resolution rule |
| tier2-closure-03-progress-tsv-ledger | OH | high | durable ledger rule |
| tier2-p1-e2e-pg-surface | OH | high | durable export rule |
| tier2-xdist-worker-crash-is-timeout-artifact | OH | high | durable artifact rule |
| tmp-01-triage-fences | OH | high | durable triage fences |
| tmp-02-structural-consumers | RS | med | structural inventory counts |
| tooling-01-grep-worktree-boundary | OH | high | durable tool boundary |
| tool-usage-metrics-p1-approver-001 | EPISODE | high | approver iteration record |
| tool-usage-metrics-p1-approver-002 | EPISODE | high | approver iteration record |
| tool-usage-metrics-p1-approver-003 | EPISODE | high | approver iteration record |
| tool-usage-metrics-p1-plan-delivered | EPISODE | high | plan-delivery state record |
| tool-usage-p1-architect-pass | EPISODE | high | architect-pass record |
| tool-usage-p1-gate-verdict | EPISODE | high | gate-verdict record |
| trustbound-01-settings-tier-agent-writable | OH | high | durable tier fact |
| trustbound-02-granting-knob-must-be-env | OH | high | durable knob rule |
| upgfix-01-refusal-census-correction | RS | med | stale-count correction; re-census rule |
| upgfix-02-repo-wide-backtick-census | RS | med | census counts (144/669) |
| upgfix-03-safety-pack-registration-gap | RS | med | pending registration obligation |
| upgrade-env-01-scripts-dir-leak | OH | high | durable scrub rule |
| upgrade-journal-writer-attribution | OH | high | durable attribution map |
| user-tz-asia-ho-chi-minh-confirmed | OH | high | durable user fact |
| uvvenv-01-no-pip-use-importlib | OH | high | durable probe rule |
| verify-01-red-receipt-overlay | OH | high | durable overlay method |
| verify-02-snapshot-l3-red-set | RS | med | by-design red-set baseline |
| verify-doc-site-table-vs-fold-diff | OH | med | rule durable; dated rider example |
| verify-merged-leg-byte-equivalence | OH | high | durable blob-id proof |
| verify-order-contract-needs-e2e-gate | OH | high | durable e2e-gate rule |
| verify-plan-conformance-term-level | OH | high | durable term-level rule |
| verify-plan-ruling-sync-grep-derived | OH | high | durable grep-derived rule |
| verify-residual-idle-claim-sweep-per-job | RS | med | residual-site state |
| verify-script-vacuous-pass-dash-shell-guard | OH | high | durable non-empty assert rule |
| views-fix10-forwarded-veto-contract | OH | high | durable veto contract |
| views-origin-gate-contract | OH | high | durable origin contract |
| views-origin-parser-nonbrowser-spellings | OH | high | durable parser fact |
| views-session-bypass-rekill2-dead | EPISODE | med | dated security-verdict record |
| views-verify-01-limiter-gate-pacing | OH | high | durable pacing contract |
| vocab-release-versions-import-coupling | OH | high | durable import-coupling trap |
| w3-f18-store-not-callable-typeerror | RS | med | open-defect routing state |
| wake-path-01-emitter-accept-skip | OH | high | durable accept-skip map |
| wake-path-01-read-failure-is-unknown | OH | high | durable fail-closed rule |
| wake-path-02-ttl-backstop-not-firing | RS | med | open-gap state |
| wedge-capture-manifest-release-proof | OH | high | durable proof carrier |
| wiring-01-capture-dispatcher-seam | OH | high | durable seam map |
| wiring-02-phase2-window | RS | low | measurement-window state |
| wiring-03-grep-scoped-miss | OH | high | durable trace rule |
| worker-completion-01-verify-disk-artifact | OH | high | durable artifact-over-report rule |
| worktree-01-venv-branch-binding | OH | high | durable binding rule |
| worktree-02-python-version-divergence | RS | high | version-pairing state |
| worktree-03-upstream-auto-set-hazard | OH | high | durable branch rule |
| worktree-cwd-shadows-import-daemon | OH | high | durable CWD rule |
| worktree-deletion-claim-verify-first | OH | high | verify-first rule; dated root example |
| worktree-env-fence-shape-recheck | OH | high | durable fence-shape rule |
| worktree-fence-pg-role-is-dev-app | OH | high | durable role facts |
| wt-04-sidecar-reclaim | OH | high | durable sidecar rule |
| wtgate-01-isolation-ladder-decides | OH | high | durable ladder rule |
| wtgate-02-base-leg-provisioning-artifacts | OH | high | durable artifact rule |
| wtgate-02-single-pack-dual-side-bar | OH | high | durable gate bar |

## 5. Findings & gaps

- **F-1 (integrity observation):** two families EXCEED the documented cap of 3 active members — `guard-inv` (4) and `prompt-assent` (4) — while `pok-family-01-cap-3-per-family-prefix` documents `pok_family_full` at the 4th member. Either grandfathered pre-cap rows or an enforcement gap. Read-only census: no action taken; flag for the pok-write-gate lane to adjudicate (the enforcement code path is in-scope for that lane).
- **F-2 (dispatch premise correction):** the guideline contains NO regex literal; its detector is the prose anchor set (L38/39/71, quoted in the Appendix). This census applied those anchors as a documented lexical proxy + semantic adjudication instead.
- **F-3 (scope):** `cutlist-2026-11-23.md` (~106-row lexical set) NOT consulted — it exists only as uncommitted runtime state in the canonical detached checkout, outside the sanctioned read scope (worktree/origin/latest).
- **F-4 (gate-design input):** 27 rows (21 EPISODE + 4 GK + 2 INCIDENT) are out-of-POK by CLASS but not rolling-state. The amended gate's judging criterion (d) covers only intra-store duplication; the boundary criterion (operational how-to vs knowledge/event — the Emperor's 2026-11-23 doctrine) is what must adjudicate these. A gate judging only redundancy would admit all 27.
- **F-5 (trust tiers):** see §3 — curated tier = 0/499, pin band = 0, verify counters never fired. The guideline has no tier-taxonomy section yet (scribe-verified: the token "tier" appears once, at L93, inside the existing inert-state measurement); this census's §3 is the measured input for that section.
- **Gaps:** creator long-tail below the top-12 cutoff not itemized (53 rows across remaining agents); per-row `id`/stamp columns summarized in §2 rather than tabulated (slug is a perfect key — 499 distinct/499); `audience_agents` column unused store-wide (0 narrowings).

## Appendix — the detector, verbatim (guideline-pok-durable-vs-rolling-state.md, L35–40, L71, L99)

> ### TEST 1 — DURABILITY (the existing durable rule, sharpened; kills snapshots)
>
> - **DURABLE (belongs in POK)** — an invariant, trap, convention, or contract. From outside it reads as a rule, not a report.
> - **Test question:** *would this still be true if today's version number, dates and directory listings were all different?* If the row's truth depends on them NOT being different, it is not durable.
> - **Mechanical smell check:** a body containing a version string, a date, a directory listing, or a count is a **snapshot** — rolling state, out of POK (convert it, §4).
> - **ROLLING (does NOT belong)** — release/staging inventories, "current version" claims, dated directory listings, per-lane inventory snapshots, counts-that-change. What happens to it in POK: it never expires, nothing marks it stale, nothing forces a refresh — **rot is silent**. Intake of this class is the disease the cap arc measured.

> - **THE CONVERSION RULE (the practical bit).** When you catch yourself writing a row that names a version, a directory, a date, or a count-that-changes, you are writing a snapshot — convert it to the INVARIANT instead.

> - Snapshot-smell probe over the store: `pok_query` for version/date/dir-shaped terms (a released version string, a dated directory listing, a "current" count); a hit whose body asserts point-in-time state is a conversion candidate.

*Source: docs/guidelines/guideline-pok-durable-vs-rolling-state.md @ worktree /home/nea/ensemble-src-wt-pok-write-gate == origin/latest, sha256 ce49dddee21fd9c389df15a19c53aeb951e603567fb0c8118119e3bef4091157 (134 lines).*

<!-- census-part-3/3-end -->
