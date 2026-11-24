# Figure 2 — Census Extract: machine-label census artifacts + kb-writer grant history

**Lane:** pok-write-gate (project `83da04de-a410-4fb5-9e92-251a99d28a52`)
**Produced by:** read-only evidence-extraction worker
**Produced at:** 2026-11-24T10:51:09.944504+00:00
**Scope:** READ-ONLY against every artifact except this one file. No DB writes, no git mutations,
no service starts/stops, no writes to the live install dir (`/home/nea/agents-ensemble`).

**Census snapshot era:** the TSVs in `evidence-pok-sweep/` are the **499-active-row** store
(post-sweep, `2026-11-23` ~13:15Z-14:17Z). The **512-active-row** store (cap-saturated at 512,
`headroom_slots=0`) is the pre-sweep 08:32Z population - see §A.4. The two are DIFFERENT
populations and their figures must not be mixed.

---

## TASK A - CENSUS ARTIFACTS

### A.1 File inventory (recursive under the evidence dir)

Root: `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep`

| absolute path | bytes | lines | sha256 | what it holds |
|---|---:|---:|---|---|
| `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md` | 36,214 | 574 | `b819401e4b5a0017423d0144143ba6c3effeef3761eea4013820b82c0d4eaf52` | Prose write-up of the extraction + mechanical-signal census method (§1 current state ... §12 other cheap mechanical signals); defines the label vocabulary, family distribution, cap state, and the 3 relocation-owed rows. |
| `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv` | 256,252 | 500 | `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1` | **THE machine-label TSV.** 19 columns WITH header + 499 data rows: the per-row mechanical-label dataset (`family_key`, `mech_labels`, `body_verbatim_escaped`, `created_by_agent`, ...). This is the file carrying a per-row label column. |
| `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.tsv` | 203,858 | 499 | `b1f5842d60031cc165a5045bf3817101cb745c840b8d5ad2247ea287673c9b59` | EXECUTOR lane's raw row dump of the same 499-row active set: 14 columns, **NO header row** (so its first line is data, not a header). Same slug set as the mech-labels TSV; lacks `mech_labels` and `family_key`. |

Line counts are `wc -l` (newline-terminated). Both TSVs end with a trailing newline.

### A.2 The machine-label TSV - exact header line (verbatim, tab-separated)

```
slug	id	family_key	domain	audience	source_tier	created_at	updated_at	last_verified	last_accessed_at	access_count	verify_count	pinned	disputed	superseded_by_id	created_by_agent	body_len	body_verbatim_escaped	mech_labels
```

Field-by-field index (1-based), as parsed from the header above:

| # | column name | role |
|---:|---|---|
| 1 | `slug` | row identifier / slug |
| 2 | `id` | row UUID |
| 3 | `family_key` | slug-prefix family |
| 4 | `domain` | POK domain (verify / artifact / environment / deploy / other / catalog) |
| 5 | `audience` | both / operation / implementation |
| 6 | `source_tier` | agent |
| 7 | `created_at` | wall-clock |
| 8 | `updated_at` | wall-clock |
| 9 | `last_verified` | wall-clock |
| 10 | `last_accessed_at` | wall-clock |
| 11 | `access_count` | counter |
| 12 | `verify_count` | counter |
| 13 | `pinned` | flag |
| 14 | `disputed` | flag |
| 15 | `superseded_by_id` | lineage |
| 16 | `created_by_agent` | **AUTHOR** |
| 17 | `body_len` | body length |
| 18 | `body_verbatim_escaped` | body text (notes/prose) |
| 19 | `mech_labels` | **CLASS-LABEL COLUMN** (`;`-joined mechanical tags) |

**Column answers:**
- row identifier / slug -> `slug` (col 1); stable UUID in `id` (col 2)
- author -> `created_by_agent` (col 16)
- class label -> `mech_labels` (col 19)
- confidence / notes column -> **NONE**. There is no confidence column and no hand-label column.
  The nearest notes column is `body_verbatim_escaped` (col 18, the row body verbatim).
  Corroborated in-repo by `evidence-rederiv-2026-11-23/funnel-and-agreement.md` §2.2:
  "There is **no** `hand_label`, `sem_label`, `semantic`, `reviewer`, `final_label`, `adjudicated`,
  or equivalent column anywhere in the worktree TSVs."

### A.3 Class vocabulary (verbatim label strings) + row counts

`mech_labels` is a `;`-joined SET of mechanical/lexical tags. The closed atom vocabulary and
the number of the 499 rows carrying each atom:

| label atom (verbatim) | rows carrying it |
|---|---:|
| `NEVER_VERIFIED` | 499 |
| `NOFAM` | 184 |
| `ZERO_ACCESS` | 109 |
| `ROLLING` (as `ROLLING:<token>`) | 109 |
| `FAMILY_AT_CAP` | 102 |
| `DUPSLUG` (as `DUPSLUG:<n>`) | 71 |
| `FAMILY_OVERCAP` | 8 |

Per-row derived class over all 499 rows (the ROLLING split - see §B.2 for why this is the
only per-row class axis the TSV can carry):

| derived class | rows |
|---|---:|
| `ROLLING:<token>` present (class **R**) | 92 |
| no `ROLLING:` token (class **NR**) | 407 |
| **total** | **499** |

Empty `mech_labels`: 0 rows. Distinct `mech_labels` combinations: 67. Distinct slugs: 499
(no duplicate slugs). Full distribution is reproducible with:

```
awk -F'\t' 'NR>1{print $19}' <mech-labels.tsv> | sort | uniq -c | sort -rn
```

Top combinations (of 67):

| count | mech_labels (verbatim) |
|---:|---|
| 110 | `NEVER_VERIFIED` |
| 96 | `NOFAM;NEVER_VERIFIED` |
| 60 | `FAMILY_AT_CAP;NEVER_VERIFIED` |
| 34 | `ZERO_ACCESS;NEVER_VERIFIED` |
| 29 | `NOFAM;ZERO_ACCESS;NEVER_VERIFIED` |
| 18 | `ROLLING:2026-11;NEVER_VERIFIED` |
| 13 | `ROLLING:2026-11;NOFAM;NEVER_VERIFIED` |
| 13 | `ROLLING:2026-11;FAMILY_AT_CAP;NEVER_VERIFIED` |
| 12 | `NOFAM;NEVER_VERIFIED;DUPSLUG:2` |
| 12 | `NEVER_VERIFIED;DUPSLUG:2` |

### A.4 Total row count - WHICH population is covered

**499 data rows** in the mech-labels TSV (500 lines = 1 header + 499 data). This is **NOT**
the 512-row active set.** The 512 figure belongs to the earlier (pre-sweep) population:

```
census-active-2026-11-23.json  (canonical sweep dir, 2026-11-23T08:32:36Z)
  counts.active                     = 512
  counts.cap                        = 512
  counts.cap_gap_active_vs_cap      = 0
  counts.headroom_slots             = 0
  distributions.by_created_by_agent_active.kb-writer = 139
```

So: the 512-row active set is the **512-row** population (08:32Z, cap-saturated); the TSVs in
`evidence-pok-sweep/` cover the **499-row** active set (post-sweep). Both the plan set and the
`census-mechanics-2026-11-23.md` header note the two eras explicitly:

- `census-mechanics-2026-11-23.md:6-10` - "`census-rows-active-2026-11-23.tsv` - EXECUTOR lane's
  artifact ... 499 lines x 14 fields, NO header ..." and "`census-rows-active-2026-11-23.mech-labels.tsv`
  - THIS lane's artifact ... 500 lines = 1 header + 499 data lines, 19 fields/line".
- `phase12-plan.md:12` - "class counts derive from this lane's census of the 499-active store
  @13:5xZ (post-sweep; **differing class vocabulary**, reconciled in the discrepancy note)".

### A.5 The two TSVs cover the same rows

```
mech-labels data rows: 499 | plain TSV data rows: 499
slug sets identical (as sets): True      # order differs
only in mech: []  |  only in plain: []
plain TSV field count per row: {14}
```

### A.6 Adjacent (non-TSV) census artifact - read for context, NOT part of this dir

`/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/` (canonical checkout; READ-ONLY here)
holds the **512-era** census that the 448/26/38 O/G/R semantic pass was run against:
`census-active-2026-11-23.json` (`generated_at` `2026-11-23T08:32:36Z`). It carries aggregate
counts and partial row lists - **not** per-row O/G/R labels.

---

## TASK B - kb-writer VERIFY FAMILY EXTRACT

### B.1 WHAT THE TSV DOES **NOT** CARRY (stated plainly)

**The TSV has NO stored operational / non-operational (or O/G/R semantic) class column.** The
only label-bearing field is `mech_labels`, whose vocabulary is
`ROLLING:<token>`, `NOFAM`, `ZERO_ACCESS`, `FAMILY_AT_CAP`, `FAMILY_OVERCAP`, `DUPSLUG:<n>`,
`NEVER_VERIFIED` - **mechanical/lexical** signals only. None of them maps to "operational" vs
"non-operational"; I am NOT inventing such a mapping.

The O/G/R class vocabulary (O = operational, G = general-knowledge, R = rolling; 448 / 26 / 38
of 512) came from the **2026-11-23 08:32Z "wanderer pass 2" semantic pass**, and its per-row
names **were never persisted** - aggregate counts only, in history row `2bf04468`. Stated in-repo
at `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:50`:

> "The 08:32Z semantic pass counted **26 G + 38 R = 64** of 512 (12.5%). **Its per-row names
> were never persisted** (counts only in history 2bf04468)".

### B.2 The stated text rule used here

**Rule (mechanical, deterministic, re-runnable):**

```
class_label      = 'ROLLING:<token>'  if mech_labels contains a 'ROLLING:' tag
                   'NO-ROLLING'       otherwise
operational_flag = 'N' if class_label starts with 'ROLLING:'   (class R - state-dependent,
                  expires with the date/version it cites; excluded by the R2 rolling rule)
                  'Y' if class_label == 'NO-ROLLING'
```

**Mapping to operational/non-operational (stated explicitly, as required):**

- `ROLLING:<token>` -> **NON-OPERATIONAL** for admission purposes (class R).
- `NO-ROLLING` -> **OPERATIONAL (class O-candidate)**.

**Honest caveat - this proxy is an UPPER BOUND on "operational".** The proxy separates R from
non-R. It CANNOT separate O from G, because no G label exists in the TSV: a general-knowledge
row is also ROLLING-free and would be counted as operational here. In the 512-era population
G was 26/512 = 5.1%; if the family carries a similar G share, ~4 of the 66 would be G, putting
true operational at ~62/79 = 78.5% rather than 83.5%. This is an INFERENCE (arithmetic on the
512-era population rate), NOT a measurement - no per-row G labels exist to measure it.

The one predicate that DOES have a measured hand-label agreement is the **ROLLING <=> hand-R**
axis, measured on the contested bands in `evidence-rederiv-2026-11-23/funnel-and-agreement.md`
§2.4 (Band B 13/16 = 81.2%; Band C 42/50 = 84.0%). That agreement is on the **R-vs-G** axis,
not on the **O-vs-G** axis.

### B.3 Family definition

**Family = `created_by_agent == 'kb-writer'` AND `domain == 'verify'`** (the POK `domain`
taxonomy value `verify` - the verify/gate pipeline domain). Family size = **79**.

```
awk -F'\t' 'NR>1 && $16=="kb-writer" && $4=="verify"' <mech-labels.tsv> | wc -l   # -> 79
```

For contrast (so the denominator choice is auditable): kb-writer has **131** rows in total
across all domains (verify 79, artifact 19, other 14, deploy 11, environment 8); the
`family_key` value `verify` proper holds only **2** rows store-wide
(`verify-01-red-receipt-overlay` (kb-writer), `verify-02-snapshot-l3-red-set` (project-manager)).
The domain-`verify` reading is the one that reproduces the relayed 83.5% figure.

### B.4 THE ROW LIST (79 rows, one per line)

Format: `slug | class_label | operational_flag(Y/N) | confidence(H/M/L) | author`

`confidence` = confidence in the **semantic** operational read of the body (H = body names a
concrete artifact of THIS repo - file, test, tool, module, command, named lane/pin system;
M = repo-anchored but the mechanism generalizes; L = the lesson would hold in a repo with no
ensemble -> a G candidate the mechanical proxy counts as operational).

```
ac-comment-token-grep-collision | NO-ROLLING | Y | M | kb-writer
access-predicate-01-narrow-stub-hides | NO-ROLLING | Y | H | kb-writer
access-predicate-02-exact-result-set-leg | NO-ROLLING | Y | H | kb-writer
anchor-ledger-02-claim-level-anchor-checks | NO-ROLLING | Y | H | kb-writer
assertion-vacuity-03-counterfactual | NO-ROLLING | Y | H | kb-writer
assertion-vacuity-03-vocabulary-membership | NO-ROLLING | Y | H | kb-writer
baseleg-02-worktree-vs-archive-choice | NO-ROLLING | Y | H | kb-writer
bash-quote-01-single-body-grep-trap | NO-ROLLING | Y | L | kb-writer
behavioral-pin-prod-seam-rule | NO-ROLLING | Y | H | kb-writer
closure-01-char-level-verify | ROLLING:2026-11 | N | H | kb-writer
craft-01-exception-swallow-reachability | ROLLING:2026-11 | N | H | kb-writer
digest-transcription-discipline | NO-ROLLING | Y | H | kb-writer
dotenv-01-scrub-before-source | NO-ROLLING | Y | H | kb-writer
e6-db-pin-skip-guard-fence | NO-ROLLING | Y | H | kb-writer
engine-boot-slo-staleness-rule-absent | NO-ROLLING | Y | H | kb-writer
engine-jobs-due-ness-shared-dev-db | NO-ROLLING | Y | H | kb-writer
engine-shadow-mode-real-exec-flags | NO-ROLLING | Y | H | kb-writer
fe-jest-gate-01-worker-pin | NO-ROLLING | Y | H | kb-writer
fe-jest-gate-02-baseline-failures | NO-ROLLING | Y | H | kb-writer
fence-pin-01-function-scoped | ROLLING:2026-11 | N | H | kb-writer
fixture-listener-01-pkill-selfmatch | NO-ROLLING | Y | L | kb-writer
fixture-listener-02-subshell-pid-loss | NO-ROLLING | Y | L | kb-writer
gate-crosslane-pins-01-class | NO-ROLLING | Y | H | kb-writer
gateleg-03-env-fence-union-scrub | ROLLING:2026-11 | N | H | kb-writer
gc-fe-surfaces-span-pin-committed-only | NO-ROLLING | Y | H | kb-writer
gc-p3-swallow-advance-class-sweep | ROLLING:2026-11 | N | H | kb-writer
gc-p3-testp11-class-identity | NO-ROLLING | Y | H | kb-writer
gc-p5-room-create-cap-race | NO-ROLLING | Y | H | kb-writer
guard-01-laundering-scope-entire-text | NO-ROLLING | Y | H | kb-writer
job-lookup-02-fail-closed-typo-pins | NO-ROLLING | Y | H | kb-writer
log-truth-pin-observed-flag-mutation | ROLLING:v0.30.11 | N | H | kb-writer
maintenance-dst-fold-warning-empty | NO-ROLLING | Y | H | kb-writer
maintenance-dst-warning-log-surface | NO-ROLLING | Y | H | kb-writer
maintenance-uniformity-smoke-band | NO-ROLLING | Y | M | kb-writer
memoir-census-pin-biting-mutations | NO-ROLLING | Y | H | kb-writer
memoir-census-pin-lane-scoped | NO-ROLLING | Y | H | kb-writer
mutation-01-guards-need-counter-mutation | NO-ROLLING | Y | H | kb-writer
mutation-02-worktree-scratch-venue | NO-ROLLING | Y | H | kb-writer
mutation-03-git-restore-byte-exact-proof | NO-ROLLING | Y | H | kb-writer
mutation-count-01-boundary-strip-masking | NO-ROLLING | Y | H | kb-writer
none-vs-zeropulse-signal-shape | ROLLING:v0.30.11 | N | H | kb-writer
optanchor-02-value-option-anchors-on-value | NO-ROLLING | Y | H | kb-writer
ownership-map-diff-audit-flag | NO-ROLLING | Y | H | kb-writer
pack-ci-top-level-unit-ungated | NO-ROLLING | Y | H | kb-writer
pack-trap-01-signal-no-exit | NO-ROLLING | Y | H | kb-writer
packs-01-conflict-keep-both-newest-first | NO-ROLLING | Y | H | kb-writer
packs-registration-02-house-grep-audit | NO-ROLLING | Y | H | kb-writer
plan-body-ledger-agreement | NO-ROLLING | Y | H | kb-writer
plan-predicate-ledger-01-attestation | NO-ROLLING | Y | H | kb-writer
predicate-discriminator-01-durable-key | NO-ROLLING | Y | H | kb-writer
pyinst-01-frozen-binary-audit-route | NO-ROLLING | Y | H | kb-writer
quarantine-01-row-names-all-reds | NO-ROLLING | Y | H | kb-writer
redclaim-01-rederive-mutation-provenance | ROLLING:2026-11 | N | H | kb-writer
review-01-remeasure-base-red-claims | ROLLING:2026-11 | N | H | kb-writer
review-01-shared-helper-blast-radius | NO-ROLLING | Y | H | kb-writer
review-pack-01-clean-checkout | ROLLING:2026-11 | N | H | kb-writer
rogue-cleanup-rider-recorded-addendum | NO-ROLLING | Y | H | kb-writer
seal-03-repin-mutation-proof-triple | NO-ROLLING | Y | H | kb-writer
sealcheck-01-readonly-git-objects-check | NO-ROLLING | Y | H | kb-writer
sealmap-03-older-base-red-not-attributable | NO-ROLLING | Y | H | kb-writer
snapshot-l3-02-base-red-set-one | NO-ROLLING | Y | H | kb-writer
source-gc-drift-guards-branch-tip-sensitive | NO-ROLLING | Y | H | kb-writer
stage-lane-evidence-capture | NO-ROLLING | Y | H | kb-writer
testgate-01-explicit-paths | NO-ROLLING | Y | H | kb-writer
testgate-02-standalone-packs | NO-ROLLING | Y | H | kb-writer
tier2-closure-02-nodeid-space-truncation | NO-ROLLING | Y | H | kb-writer
upgfix-02-repo-wide-backtick-census | NO-ROLLING | Y | H | kb-writer
upgfix-03-safety-pack-registration | NO-ROLLING | Y | H | kb-writer
verify-01-red-receipt-overlay | NO-ROLLING | Y | H | kb-writer
verify-plan-conformance-term-level | NO-ROLLING | Y | H | kb-writer
views-fix10-forwarded-veto-contract | NO-ROLLING | Y | H | kb-writer
views-origin-gate-contract | NO-ROLLING | Y | H | kb-writer
views-origin-parser-nonbrowser-spellings | NO-ROLLING | Y | H | kb-writer
views-session-bypass-rekill2-dead | ROLLING:2026-11 | N | H | kb-writer
wake-path-01-emitter-accept-skip | NO-ROLLING | Y | H | kb-writer
wake-path-01-read-failure-is-unknown | NO-ROLLING | Y | H | kb-writer
wake-path-02-ttl-backstop-not-firing | ROLLING:2026-11 | N | H | kb-writer
worker-completion-01-verify-disk-artifact | NO-ROLLING | Y | H | kb-writer
worktree-deletion-claim-verify-first | ROLLING:2026-11 | N | H | kb-writer
```

### B.5 AGGREGATE COUNTS (computed from the rows above)

| metric | value |
|---|---:|
| family size (kb-writer and domain=verify) | **79** |
| operational (flag Y, `NO-ROLLING`) | **66** |
| non-operational (flag N, `ROLLING:`) | **13** |
| **pct operational** | **83.54%** (66/79) |

**Cross-check against the relayed figure:** the relayed "kb-writer family 83.5% operational"
is reproduced as 83.54% = 66/79 under the stated rule. The relayed figure carries **no
in-branch source** (approver `S-1` HARD GATE, `plan-overview.md:319`);
this is a NEW re-derivation, not a copy.

**Confidence spread among the 66 operational rows:**

| confidence | rows | meaning |
|---|---:|---|
| H | 61 | body names a concrete artifact of this repo |
| M | 2 | repo-anchored, mechanism generalizes |
| L | 3 | G candidate (counted operational by the mechanical proxy) |

**Sensitivity band for the parent's choice:**

- mechanical proxy (stated rule, mechanical axis only): **66/79 = 83.54%**
- H+M only (exclude the L / G-candidate rows): **63/79 = 79.75%**
- H only (strictest): **61/79 = 77.22%**

### B.6 Reproduction command

```bash
# the 79-row extract (author col 16, domain col 4, labels col 19)
awk -F'\t' 'NR>1 && $16=="kb-writer" && $4=="verify"' \
  /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv \
  | awk -F'\t' '{r=($19 ~ /ROLLING:/) ? "N" : "Y"; print $1" | "$19" | "r}' \
  | sort
```

---

## TASK C - kb-writer TOOL-GRANT HISTORY (dual-grant mechanism)

**Method:** read-only git in `/home/nea/ensemble-src-wt-pok-write-gate` - `git log`, `git show`, `git rev-parse`.
No checkout / commit / stash / reset / push performed.

### C.1 CURRENT state of `agents/kb-writer/meta.json` (worktree file)

```json
{
  "id": "kb-writer",
  "name": "KB Writer",
  "description": "Simple knowledge base writer — takes knowledge text, splits by domain/category, and inserts each segment via rag_insert_text. Does NOT expose RAG graph tools.",
  "icon": "✍️",
  "color": "accent-purple",
  "version": "1.0.0",
  "llm_model": "quick",
  "blueprint_inactive": true,
  "innate_skills": [],
  "tools": {
    "allow": ["rag_insert_text", "tool_help", "time", "nest-report"]
  },
  "team_members": []
}
```

Current `tools.allow` - **verbatim tool-name strings**:

```
["rag_insert_text", "tool_help", "time", "nest-report"]
```

-> **NO `pok` / `pok_*` tool present.** A RAG-insert tool (`rag_insert_text`) IS still present.

### C.2 Commit history touching the file

```
fd7dc6480|2026-11-24 06:50:51 +0000|giter|feat(pok): Phase 4 — kb-writer grant removal + compensating capture duty
16b4e70b2|2026-11-07 08:27:33 +0000|giter|feat(agents): grant the pok category to all 45 agents — B4 sweep + F-1 ceremony (POK Phase 1 T5)
e9d79d625|2026-11-05 11:24:50 +0000|giter|feat(agents): grant the nest-report category to all 40 agents and re-pin the allow-array constants
7d1c5aeee|2026-08-03 01:12:33 +0700|Worker|feat: Project Blueprint Phase 2 — injection integration
8bdf3cf8a|2026-07-22 12:43:17 +0700|Kha|fix: address review feedback — test fix, tool literalization, doc accuracy
f7a8985c8|2026-07-22 12:00:44 +0700|Kha|fix: sync frontend KB_AGENT_IDS and clean up kb-writer agent
4eb1b80fe|2026-07-22 11:55:15 +0700|Kha|feat: add kb-writer agent — simple RAG text insertion specialist
```

### C.3 Per-commit diff hunks for `agents/kb-writer/meta.json`

**`4eb1b80fe`**

```
COMMIT 4eb1b80fec1fc5d8543078b25af49456292f86ec
DATE  2026-07-22 11:55:15 +0700
AUTHOR Kha
SUBJ  feat: add kb-writer agent — simple RAG text insertion specialist
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
new file mode 100644
index 000000000..8f812753f
--- /dev/null
+++ b/agents/kb-writer/meta.json
@@ -0,0 +1,14 @@
+{
+  "id": "kb-writer",
+  "name": "KB Writer",
+  "description": "Simple knowledge base writer — takes knowledge text, splits by domain/category, and inserts each segment via rag_insert_text. Does NOT expose RAG graph tools.",
+  "icon": "✍️",
+  "color": "accent-purple",
+  "version": "1.0.0",
+  "llm_model": "quick",
+  "innate_skills": [],
+  "tools": {
+    "allow": ["rag_insert_text", "help", "time"]
+  },
+  "team_members": []
+}
\ No newline at end of file
```

**`f7a8985c8`**

```
COMMIT f7a8985c8b06b8c1fe014cdaf1546f3558419793
DATE  2026-07-22 12:00:44 +0700
AUTHOR Kha
SUBJ  fix: sync frontend KB_AGENT_IDS and clean up kb-writer agent
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index 8f812753f..0f9640e4d 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -11,4 +11,4 @@
     "allow": ["rag_insert_text", "help", "time"]
   },
   "team_members": []
-}
\ No newline at end of file
+}
```

**`8bdf3cf8a`**

```
COMMIT 8bdf3cf8a194abf76361cd545fc2cec7d471223c
DATE  2026-07-22 12:43:17 +0700
AUTHOR Kha
SUBJ  fix: address review feedback — test fix, tool literalization, doc accuracy
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index 0f9640e4d..a49bddd1f 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -8,7 +8,7 @@
   "llm_model": "quick",
   "innate_skills": [],
   "tools": {
-    "allow": ["rag_insert_text", "help", "time"]
+    "allow": ["rag_insert_text", "tool_help", "time"]
   },
   "team_members": []
 }
```

**`7d1c5aeee`**

```
COMMIT 7d1c5aeeed010985b0ad81cf94dfaa8907e1b1a5
DATE  2026-08-03 01:12:33 +0700
AUTHOR Worker
SUBJ  feat: Project Blueprint Phase 2 — injection integration
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index a49bddd1f..380197eb4 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -6,6 +6,7 @@
   "color": "accent-purple",
   "version": "1.0.0",
   "llm_model": "quick",
+  "blueprint_inactive": true,
   "innate_skills": [],
   "tools": {
     "allow": ["rag_insert_text", "tool_help", "time"]
```

**`e9d79d625`**

```
COMMIT e9d79d62510afbe34a670a0aaee802d2f8252f49
DATE  2026-11-05 11:24:50 +0000
AUTHOR giter
SUBJ  feat(agents): grant the nest-report category to all 40 agents and re-pin the allow-array constants
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index 380197eb4..72964514b 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -9,7 +9,7 @@
   "blueprint_inactive": true,
   "innate_skills": [],
   "tools": {
-    "allow": ["rag_insert_text", "tool_help", "time"]
+    "allow": ["rag_insert_text", "tool_help", "time", "nest-report"]
   },
   "team_members": []
 }
```

**`16b4e70b2`**

```
COMMIT 16b4e70b21c4522babb01d5dc3ebe5ff4cbf0fc8
DATE  2026-11-07 08:27:33 +0000
AUTHOR giter
SUBJ  feat(agents): grant the pok category to all 45 agents — B4 sweep + F-1 ceremony (POK Phase 1 T5)
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index 72964514b..12cfbf1ca 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -9,7 +9,7 @@
   "blueprint_inactive": true,
   "innate_skills": [],
   "tools": {
-    "allow": ["rag_insert_text", "tool_help", "time", "nest-report"]
+    "allow": ["rag_insert_text", "tool_help", "time", "pok", "nest-report"]
   },
   "team_members": []
 }
```

**`fd7dc6480`**

```
COMMIT fd7dc64808a498057cf37dcb6650bb427e6b38b9
DATE  2026-11-24 06:50:51 +0000
AUTHOR giter
SUBJ  feat(pok): Phase 4 — kb-writer grant removal + compensating capture duty
diff --git a/agents/kb-writer/meta.json b/agents/kb-writer/meta.json
index 12cfbf1ca..72964514b 100644
--- a/agents/kb-writer/meta.json
+++ b/agents/kb-writer/meta.json
@@ -9,7 +9,7 @@
   "blueprint_inactive": true,
   "innate_skills": [],
   "tools": {
-    "allow": ["rag_insert_text", "tool_help", "time", "pok", "nest-report"]
+    "allow": ["rag_insert_text", "tool_help", "time", "nest-report"]
   },
   "team_members": []
 }
```

### C.4 VERDICT - did the file EVER grant BOTH a RAG-insert tool AND a POK tool?

**YES - CONFIRMED.**

| fact | value |
|---|---|
| dual-grant window OPENED | commit **`16b4e70b21c4522babb01d5dc3ebe5ff4cbf0fc8`**, `2026-11-07 08:27:33 +0000`, author `giter`, subject "feat(agents): grant the pok category to all 45 agents - B4 sweep + F-1 ceremony (POK Phase 1 T5)" |
| dual-grant window CLOSED | commit **`fd7dc64808a498057cf37dcb6650bb427e6b38b9`**, `2026-11-24 06:50:51 +0000`, author `giter`, subject "feat(pok): Phase 4 - kb-writer grant removal + compensating capture duty" |
| duration of dual grant | 2026-11-07 08:27:33Z -> 2026-11-24 06:50:51Z = **17 days 22h 23m** |
| "before" state (pre-`16b4e70b2`) | `"allow": ["rag_insert_text", "tool_help", "time", "nest-report"]` |
| "after" state (dual-grant, `16b4e70b2` .. `fd7dc6480`) | `"allow": ["rag_insert_text", "tool_help", "time", "pok", "nest-report"]` |
| "after" state (current, post-`fd7dc6480`) | `"allow": ["rag_insert_text", "tool_help", "time", "nest-report"]` |
| CURRENT state on this branch | **no `pok` grant** - RAG-insert only + `tool_help`, `time`, `nest-report` |

**Mechanism detail - the `pok` string is a TOOL-CATEGORY grant, not a single tool.**
`daemon/tools/_tool_registry.py` (CATEGORY_MODULES):

```python
"pok": "daemon.tools.pok",
```

and the module's tool names (identical at `origin/latest` and at the recorded base
`a1130e257a3c3bb3929455aec28cacafcbfaa1a7`, lines 1951-1953):

```python
"pok_upsert",
"pok_query",
"pok_curate",
```

(`pok_gate_answer` was added later, on this branch, by Wave-2 Phase 8 / T8.5 - it is NOT
part of the historical dual grant.) The in-repo comment at `_tool_registry.py:1292-1300`
names the mechanism explicitly: "the three project operating-knowledge tools
(pok_upsert / pok_query / pok_curate) ... grants flow through the per-agent
``tools.allow: [\"pok\"]`` entries landed by the B4 sweep (40-agent class; watcher excluded -
empty allow inherits-all)". The RAG side is a direct tool grant: `rag_insert_text`
(`_tool_registry.py:1965`), i.e. the **kb-writer RAG-insert capability**.

=> **Mechanism (fact, from history):** during 2026-11-07 -> 2026-11-24, `kb-writer` held a
**RAG-insert tool** (`rag_insert_text`) **and** a **POK tool-category** grant (`pok` ->
`pok_upsert` / `pok_query` / `pok_curate`) **simultaneously**. That is the historical
dual-grant surface the current file state no longer proves.

### C.5 BASE REFS - pok/rag grant state at the two refs

```
$ git show origin/latest:agents/kb-writer/meta.json
{
  "id": "kb-writer",
  "name": "KB Writer",
  "description": "Simple knowledge base writer — takes knowledge text, splits by domain/category, and inserts each segment via rag_insert_text. Does NOT expose RAG graph tools.",
  "icon": "✍️",
  "color": "accent-purple",
  "version": "1.0.0",
  "llm_model": "quick",
  "blueprint_inactive": true,
  "innate_skills": [],
  "tools": {
    "allow": ["rag_insert_text", "tool_help", "time", "pok", "nest-report"]
  },
  "team_members": []
}
$ git show a1130e257a3c3bb3929455aec28cacafcbfaa1a7:agents/kb-writer/meta.json
{
  "id": "kb-writer",
  "name": "KB Writer",
  "description": "Simple knowledge base writer — takes knowledge text, splits by domain/category, and inserts each segment via rag_insert_text. Does NOT expose RAG graph tools.",
  "icon": "✍️",
  "color": "accent-purple",
  "version": "1.0.0",
  "llm_model": "quick",
  "blueprint_inactive": true,
  "innate_skills": [],
  "tools": {
    "allow": ["rag_insert_text", "tool_help", "time", "pok", "nest-report"]
  },
  "team_members": []
}
```

| ref | `tools.allow` (verbatim) | RAG tool? | POK tool? |
|---|---|---|---|
| `origin/latest` (= `bcebe77250ab6ae07212cfafc33b2a8e82608d64`) | `["rag_insert_text", "tool_help", "time", "pok", "nest-report"]` | **YES** | **YES** (`pok`) |
| `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` (recorded base_sha) | `["rag_insert_text", "tool_help", "time", "pok", "nest-report"]` | **YES** | **YES** (`pok`) |
| branch HEAD `97f269ea42ecc75aa116f72d7dc8b0f6b09ea5b0` | `["rag_insert_text", "tool_help", "time", "nest-report"]` | YES | **NO** |

=> **At BOTH refs the dual grant is present.** The removal is a branch-only, UNPUSHED change:
`fd7dc6480` is **not** an ancestor of `origin/latest`
(`git merge-base --is-ancestor fd7dc6480 origin/latest` -> false; `git branch -a --contains
fd7dc6480` -> `* feature/pok-write-gate` only).

### C.6 Repository / provenance facts

```
worktree        : /home/nea/ensemble-src-wt-pok-write-gate
branch          : feature/pok-write-gate
HEAD            : 97f269ea42ecc75aa116f72d7dc8b0f6b09ea5b0
origin/latest   : bcebe77250ab6ae07212cfafc33b2a8e82608d64
base_sha        : a1130e257a3c3bb3929455aec28cacafcbfaa1a7
git status      : (clean - no output)
shallow clone   : false
```

History is **complete** (not shallow) - the file's creation commit `4eb1b80fe`
(2026-07-22 11:55:15 +0700) is reachable, so the full grant timeline above is exhaustive.

---

## LIMITATIONS

1. **No stored operational/non-operational class exists in the TSV.** §B's classification is
   the stated mechanical rule (ROLLING <=> R), not a census-stored class. The 83.5% is therefore
   an UPPER BOUND on "operational" (a G row would also be ROLLING-free). No per-row G label
   exists anywhere in the artifacts (the 08:32Z semantic pass never persisted its per-row
   names) - so the O-vs-G split is **NOT measurable** from these artifacts; only the R-vs-non-R
   split is.
2. **Two different populations.** §B is measured on the **499-row** (post-sweep, 2026-11-23
   ~13:15Z-14:17Z) active set. The relayed 448/26/38-of-512 and the kb-writer=139 author
   count belong to the **512-row** (08:32Z, pre-sweep) set. kb-writer's row count moved
   **139 -> 131** between the two eras; the 79-row verify family is the 499-era number and has
   no 512-era counterpart in these artifacts.
3. **The "83.5% operational" coincidence.** 66/79 = 83.54% reproduces the relayed figure
   exactly under the stated rule, which is strong support that this is the intended
   denominator/predicate. It is still a **reproduction by a proxy rule**, not a recovery of
   the original semantic pass - the original per-row labels do not exist.
4. **TASK C is history-derived and complete**; no limitation on that leg. The one caveat is
   scope: the removal commit `fd7dc6480` is branch-local and unpushed, so `origin/latest`
   still carries the dual grant.
5. **Not attempted (out of scope):** the third trio figure (kb-writer **65-of-81 same-run
   dual-writes**) and the **7-18% reject rates** were NOT re-derived here - this task was
   scoped to A / B / C. The 81-row RAG-duplicate population is not present in these artifacts
   as a per-row file (the overlap classes came from the 512-era semantic pass).

---

## APPENDIX - all decisive commands used

```bash
# TASK A
ls -la /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/
find /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/ -type f | sort ; stat -c%s ; wc -l ; sha256sum
head -3 /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv | cat -A
awk -F'\t' 'NR>1{print $19}' /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv | sort | uniq -c | sort -rn
awk -F'\t' 'NR>1{print $3}' /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv | sort | uniq -c
awk -F'\t' 'NR>1{print $16}' /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv | sort | uniq -c | sort -rn
awk -F'\t' 'NR>1 && $16=="kb-writer" && $4=="verify"' /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-rows-active-2026-11-23.mech-labels.tsv | wc -l

# TASK C
git -C /home/nea/ensemble-src-wt-pok-write-gate log --pretty=format:'%h|%ad|%an|%s' --date=iso -- agents/kb-writer/meta.json
git -C /home/nea/ensemble-src-wt-pok-write-gate show --format='' --unified=3 <commit> -- agents/kb-writer/meta.json
git -C /home/nea/ensemble-src-wt-pok-write-gate show origin/latest:agents/kb-writer/meta.json
git -C /home/nea/ensemble-src-wt-pok-write-gate show a1130e257a3c3bb3929455aec28cacafcbfaa1a7:agents/kb-writer/meta.json
git -C /home/nea/ensemble-src-wt-pok-write-gate merge-base --is-ancestor fd7dc6480 origin/latest
git -C /home/nea/ensemble-src-wt-pok-write-gate branch -a --contains fd7dc6480
git -C /home/nea/ensemble-src-wt-pok-write-gate show origin/latest:daemon/tools/_tool_registry.py | grep -nE '^\s*"pok(_upsert|_query|_curate|_gate_answer)?",?$'
```

_Generated 2026-11-24T10:51:10.462738+00:00 - read-only extraction; no DB write, no git mutation._

---

## FOLLOW-UP (Q1/Q2/Q3)

**Append-only.** The A/B/C content above is unchanged. This section was appended
**2026-11-24T11:01:12.604700+00:00** by the same read-only worker
(same fence: one writable target = this file; no DB write, no git mutation, no service action).

### Q1 - THE MECHANICAL RULES (verbatim, with file+line)

Source of every rule: `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md`
(sha256 `b819401e4b5a0017423d0144143ba6c3effeef3761eea4013820b82c0d4eaf52`, 36,214 bytes, 574 lines).

#### Q1.0 The closed label vocabulary + JOIN ORDER (precedence)

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:18`:

> **Mechanical labels (materialised per row in `census-rows-active-2026-11-23.mech-labels.tsv`) — join order (R3):** DUP_EXACT; DUP_NEAR; ROLLING; TRIVIA_SHORT; LONG_BODY; NOFAM; FAMILY_OVERCAP; FAMILY_AT_CAP; ZERO_ACCESS; NEVER_VERIFIED; STALE_30D; DUPSLUG. `NONE` when empty. ROLLING uses the **body-only** surface (R4); body∪slug variant reported in §4.

⇒ the label set is closed and the `;`-joined order in a row's `mech_labels` cell follows that
sequence. `NONE` when empty.

#### Q1.1 `ROLLING:<token>` - the exact rule

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:105`:

> **Surface used in the `census-rows-active-2026-11-23.mech-labels.tsv` ROLLING labels: body-only substring match (R4).** Regex (case-sensitive): `v0\.\d+(?:\.\d+)*|releases/|2026-\d{2}|staged|promote`.

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:107-108`:

> - Body-only rolling rows: **92** (MEASURED).
> - Body∪slug variant: **96** (MEASURED).

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:236-242` (the SQL that realises it):

```sql
**SQL (pattern, body-only):**
```sql
SELECT slug FROM public.project_operating_knowledge
WHERE project_id='83da04de-a410-4fb5-9e92-251a99d28a52' AND status='active'
  AND (body ~ 'v0\.[0-9]+|releases/|2026-[0-9]{2}|staged|promote');
```
(MEASURED: 92 rows. Note PG `~` is case-sensitive by default.)
```

**What `<token>` is** - `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:234`:

> (Per-row matched tokens are carried in the `census-rows-active-2026-11-23.mech-labels.tsv` `mech_labels` column as `ROLLING:<token>`.)

`<token>` is **the literal matched string**, not a date and not a version in general: it is one of
`2026-NN` (a YYYY-MM month), `v0.N[.N…]` (a version), `releases/`, `staged`, or `promote`.
Token-frequency table, `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:111-135`:

```
**Token frequency (body-only, case-sensitive):**
| token | count |
|---|---|
| 2026-11 | 59 |
| promote | 13 |
| staged | 7 |
| releases/ | 7 |
| v0.31.16 | 2 |
| v0.31.2 | 2 |
| v0.31.10 | 2 |
| v0.30.11 | 2 |
| v0.31.1 | 2 |
| v0.29.6 | 2 |
| v0.31.22 | 1 |
| v0.31.39 | 1 |
| v0.31.40 | 1 |
| v0.31.3 | 1 |
| v0.29.5 | 1 |
| v0.30.2 | 1 |
| v0.31.4 | 1 |
| v0.29.0 | 1 |
| v0.31.0 | 1 |
| v0.30.5 | 1 |
| v0.27.5 | 1 |
| (sum of version tokens) | 92 rows total |
```

Case-sensitivity: `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:137` —

> Case-insensitive variant: identical counts (no case variants observed). MEASURED.

and `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:242`:

> (MEASURED: 92 rows. Note PG `~` is case-sensitive by default.)

**Two surfaces, two counts:** body-only = **92** rows (the surface actually used for the
`mech_labels`); body∪slug = **96** rows (reported, not used).

#### Q1.2 `NOFAM`

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:456`:

> - NOFAM count: 184 active rows (36.9% of active set) — slugs not matching `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$`. MEASURED.

⇒ regex: `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$` — a slug that does NOT match is
`NOFAM`. Note this is a **slug** rule, not a body rule.

#### Q1.3 `ZERO_ACCESS`

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:368` (table row, active-rows column):

> | access_count = 0 | 134 | 109 |

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:382`:

> 

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:393` (SQL):

>        count(*) FILTER (WHERE access_count=0) AS zero_access

⇒ `access_count = 0`.

#### Q1.4 `NEVER_VERIFIED`

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:378`:

> - The trust machinery is entirely unused: **zero pinned, zero disputed, zero curated** rows anywhere in the project (all statuses). `NEVER_VERIFIED` applies to 100% of rows (verify_count=0 store-wide) — the `verify_count` column is effectively dead.

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:391` (SQL):

>        count(*) FILTER (WHERE verify_count=0) AS never_verified,

⇒ `verify_count = 0`.

#### Q1.5 `FAMILY_AT_CAP` / `FAMILY_OVERCAP`

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:407-409`:

> - Distinct family keys: 381 (out of 499 active rows; 184 rows are NOFAM).
> - Over-cap families (>3 active members): **2** (vs 08:32Z's 21).
> - Families at exactly 3 active members: **34** (vs 08:32Z's 36).

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:523` (the constant):

> | POK_FAMILY_MAX | 3 | 118 |

⇒ with `POK_FAMILY_MAX = 3`: **FAMILY_OVERCAP** = the row's `family_key` has **> 3** active
members; **FAMILY_AT_CAP** = its `family_key` has **exactly 3** active members. Both are
per-family (not per-row) counts over the active set. Note §8 reports 34 families at exactly 3
and 2 over-cap, yet the *row* label counts are 102 and 8 — because a family with exactly 3
members labels all 3 of its rows.

#### Q1.6 `DUPSLUG:<n>`

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:546`:

> 5. **83 slugs have >1 generation** (16.6% of distinct active slugs) — multi-generation churn; max generations for a single slug = 8 (`auth-lane-scrub-ensemble-auth-env`). 23 slugs have 3+ generations. DUPSLUG:<n> label applies to those active rows (materialised per row in `census-rows-active-2026-11-23.mech-labels.tsv`).

⇒ `n` = the number of **generations** (rows sharing that slug across ALL statuses**).
Empirical `n`-distribution measured from the 499-row TSV's own `mech_labels`:

| n | rows carrying `DUPSLUG:<n>` |
|---:|---:|
| 2 | 50 |
| 3 | 15 |
| 4 | 3 |
| 5 | 1 |
| 7 | 1 |
| 8 | 1 |
| **total** | **71** (71 distinct slugs) |

`n = 8` matches §12.5’s “max generations for a single slug = 8”. §12.5’s “83 slugs have >1
generation” counts slugs across all statuses; the label is applied only to their **active**
rows, hence 71 labelled rows.

#### Q1.7 remaining closed-list labels (for completeness)

- `DUP_EXACT` — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:87`: **(a) Exact-body duplicate groups among active rows: 0 groups.** MEASURED — no two active rows share an identical body (raw, stripped, or full sha256). `DUP_EXACT:<sha256-8>` label applies to 0 rows.
- `DUP_NEAR` — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:97`: **(b) Near-duplicate candidate pairs (token-set Jaccard >= 0.55 over lowercased `[a-z0-9]+` tokens): 0 pairs.** MEASURED — maximum observed Jaccard across all C(499,2) pairs = 0.4146 (`boot-smoke-02-frozen-bytecode-interpreter` vs `pyinst-03-release-binary-interpreter-version`). Top-10 scores: 0.4146, 0.3913, 0.3721, 0.3704, 0.3333, 0.3265, 0.3261, 0.3256, 0.3125, 0.3111. Count >=0.4: 1 pair. Count >=0.5: 0. Count >=0.55: 0. Count >=0.65: 0.
- `TRIVIA_SHORT` — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:260`: - TRIVIA_SHORT (body < 60 chars): **0 rows.** MEASURED — no trivially short bodies.
- `LONG_BODY` — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:261`: - LONG_BODY (>240 chars): **0 rows** (max is exactly 240). But **12 rows sit at the 240-char ceiling** — at the design limit, potential truncation risk. MEASURED.
- `STALE_30D` — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:379-380`:
  > - `STALE_30D` label applies to **0 active rows** (no last_verified older than 2026-10-24; the oldest verified row is within 30 days).
  > - NULL last_verified: 71 active rows (14.2%) — these get no STALE_30D label per R6.

#### Q1.8 PROOF the rules re-apply to LIVE rows (recomputed from the TSV’s own columns)

Each rule above was re-implemented independently and recomputed over the 499 active rows,
then compared against the label stored in `mech_labels`:

```
label             recomputed   stored  match
ROLLING                   92        92  OK
NOFAM                    184       184  OK
ZERO_ACCESS              109       109  OK
NEVER_VERIFIED           499       499  OK
FAMILY_AT_CAP            102       102  OK
FAMILY_OVERCAP             8         8  OK
```

The ROLLING recomputation used exactly the doc’s regex on `body_verbatim_escaped`
(`v0\.\d+(?:\.\d+)*|releases/|2026-\d{2}|staged|promote`) → 92 rows; the body∪slug variant
recomputed to 96, matching §4:108. **Every rule is exactly re-appliable to live rows.**

**Snapshot caveat carried forward** — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:12`:

> > **Snapshot-drift observation (MEASURED 2026-11-23 ~14:17Z; does NOT change any number in this MD).** The live store grew after this lane's 12:29Z read-only dump: 679 -> 682 rows total, active 499 -> 501. Added: `engine-diff-audit-reanchor-discipline` (active), `release-journal-pack-02-linux-prefail` (active), `job-lookup-01-canonical-id-seam` (new id, active; the prior id moved to superseded). 17 rows changed `access_count`/`last_accessed_at` only (read-side counters). The `.mech-labels.tsv` dataset is therefore a **12:29Z snapshot of 499 active rows** and does not include the post-snapshot additions; a fresh dump would add those rows and shift the derived counts.

---

### Q2 - THE THREE RELAYED NUMBERS' IN-BRANCH ORIGIN

**Search performed** (read-only, absolute paths, `grep -rn`):

- Roots: (R1) `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/` (all subdirs incl. the
  evidence dirs); (R2) `/home/nea/ensemble-src-wt-pok-write-gate/.agents/` (approver notes, tester RESULTS, shared/);
  (R3) `/home/nea/ensemble-src/.agents/shared/` (canonical checkout — READ ONLY).
- Tokens: `83.5`, `83.54`, `66/79`, `65-of-81`, `65 of 81`, `65/81`, `81`, `85 rows`, `85-row`,
  `verify family`, `7-18`, `7–18`, `reject rate`, `reject-rate`, `reject_rate`, `leader 8%`,
  `8% reject`, `simulat`, `dual-write`, `RAG-duplicate`, `ROUTE-RAG`, `verdict mix`.
- Plus a `shared_meta_kv` read (READ-ONLY, no write) of the lane’s leader addenda.

#### Q2.a “65 of 81 RAG-duplicate rows written into BOTH stores in the SAME kb-writer run (±0s)”

**In-branch FILE source: NOT FOUND.** Every in-branch hit is the gate text *quoting the figure
in order to flag it as unsourced*:

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/phase12-plan.md:17`:

  > - kb-writer same-run dual-write mechanism (65 of 81 RAG-duplicate rows); kb-writer family 83.5% operational; reject rates 7–18% across all authors. **[S-1 HARD GATE: all three have NO in-branch source — re-derive + stamp (snapshot time + provenance) at authoring before the guideline diff is presented, or drop with explicit disclosure; the diff is BLOCKED until each is resolved. See plan-overview T12.2]**

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/approver-pok-write-gate.md:119`:

  > 1. **Phase-12 authoring is load-bearing:** the kb-writer trio (65-of-81 / 83.5% / 7–18%) has NO in-branch source — T12.2's "re-derived and stamped at authoring, BEFORE the diff is presented" must genuinely execute, or unbacked figures reach the Emperor's draft. (448/26/38 of 512 now present at funnel:130 — improved.)

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/plan-overview.md:319` (T12.2 HARD GATE):

  > | 12 | T12.2 Fold measured numbers (census 499 @13:5xZ + composition; admissibility 87.5% machine-parse 448 operational / 26 general-knowledge / 38 rolling of 512 — UNVERIFIED in-branch, standing flag unchanged; hand-label agreement = the MEASURED bands from the 2026-11-24 re-derivation — Band B 13/16 = 81.2%, Band C 42/50 = 84.0%, each with its `ROLLING:` ⟺ hand-R/hand-G predicate; the relayed hand-label agreement figure (no denominator; per-row labels never persisted) is DROPPED as unbacked; retrieval legs per evidence-rederiv-2026-11-23/funnel-and-agreement.md Figure 1 — 568 active @2026-11-24T01:53–02:05Z (drifting to 573), 431 ever retrieved by queries, 384 ever injected (retention-bounded lower bound), 518 cited by exact kebab-token + report-level 156 of 1,412 project reports — NOT a nested funnel; the relayed nested retrieval chain (four legs with per-stage percentages) is DROPPED as unbacked, its figures not reproduced here by design (see the fold ledger)). **HARD GATE (S-1 / approver-002 condition 1): the kb-writer trio — (a) 65-of-81 same-run dual-writes, (b) 83.5% operational verify family, (c) 7-18% reject rates — has NO in-branch source; each MUST be re-derived and stamped (snapshot time + provenance) at Phase-12 authoring BEFORE the guideline diff is presented, or dropped with explicit disclosure. The diff may NOT be presented until all three are resolved. Mirrored in phase12-plan.md task 2.** | Doc edit | guideline measured-state | diff review | n/a (docs) |

**Actual origin — a shared_meta_kv row, not a file.** `pok-write-gate.addendum-leader-1`
(leader, via Ari, 2026-11-23) verbatim:

> PLANNING ADDENDUM (leader, via Ari, 2026-11-23) - fold into plan, do not restart. || ITEM 1
> RIDER ADOPTED (caller-side phase, own pins): remove 'pok' tool grant from
> agents/kb-writer/meta.json. Measured basis: of 81 RAG-DUPLICATE POK rows, 65 (80%) written
> into BOTH stores in the SAME kb-writer run (±0s); root cause = kb-writer meta.json grants
> BOTH rag_insert_text AND pok while prompts mention only RAG - POK writes ride the grant, not
> an instruction. By-author redundancy: kb-writer 48% (139 rows) vs worker 7% vs leader 0/50.

**The `81` IS in-branch — as a class count only.**
`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/store-effectiveness-rag-overlap-brief.md:55-61`:

```
| class | rows | % |
|---|---|---|
| RAG-DUPLICATE | 81 | 15.8% |
| NEITHER / rolling∧dup | 16 | 3.1% |
| redundant-or-rolling total | 97 | 19.0% |
| BOTH-USEFUL | 337 | 65.8% |
| POK-ONLY | 78 | 15.2% |
```

But that brief states it has **no per-row names** — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/store-effectiveness-rag-overlap-brief.md:63-64`:

> - Structural corroboration (this lane): the duplication mechanism is real — the kb-curator first-turn mirror duty pushes KB docs into RAG, and 24 distinct POK writer-agents overlap the `experience()` writer population, so lane-captured gotchas land in both stores by construction.
> - Top overlapping pairs: NOT reproducible live this run (probe blockage above). No pair list is fabricated; the census's pair-level detail lives in its own artifact.

⇒ **the 65-of-81 split is NOT re-derivable from any persisted artifact**: the 81-row
RAG-DUPLICATE population is a class count with no per-row join key in the repo.

#### Q2.b “kb-writer’s verify family (85 rows) is 83.5% OPERATIONAL”

**In-branch FILE source for `83.5`: NOT FOUND as a source.** The only file hits are the same
gate/approver text that flags it:

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/approver-pok-write-gate.md:45`:

  > 3. **Phase-12 measured-state set not branch-confirmable** (admissibility 448/26/38 of 512 + 82.8% without denominator; kb-writer trio 65-of-81 / 83.5% / 7–18%; boundary split ~87/7/6; 109 vs 112 never-retrieved; binds-vs-never behavior claims): disclosed canonical-only with a T2 re-derivation step — **CONDITION TRACKED** (the caller's standing condition): exact per-figure timestamps + provenance stamped at Phase-12 authoring BEFORE the guideline diff is presented (`phase12-plan.md:24`); verify the step covers every figure in this cluster, including the 82.8% denominator.

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/phase12-plan.md:24`:

  > | 2 | Fold corrected numbers above into measured-state section WITH snapshot times (exact per-figure timestamps RE-DERIVED and STAMPED at Phase-12 authoring, before the diff is presented — not copied from the snapshot-era line) — admissibility 87.5% machine-parse (448 operational / 26 general-knowledge / 38 rolling of 512; UNVERIFIED in-branch, standing flag); hand-label agreement = MEASURED bands from the 2026-11-24 re-derivation (Band B 13/16 = 81.2%, Band C 42/50 = 84.0%, each with its `ROLLING:` ⟺ hand-R/hand-G predicate; the relayed hand-label agreement figure is DROPPED as unbacked (no denominator; per-row labels never persisted)); retrieval legs per evidence-rederiv-2026-11-23/funnel-and-agreement.md Figure 1 — 568 active @2026-11-24T01:53–02:05Z (drifting to 573), 431 ever retrieved BY QUERIES (query-only semantics), 384 ever injected (retention-bounded lower bound), 518 cited by exact kebab-token + report-level 156 of 1,412 project reports — NOT a nested funnel, no cross-leg percentages; entry budget (8 rows/view) binds, char budget never does | briefs + addendum-3 + re-derivation artifact | Numbers present, exact stamps re-derived at authoring, discrepancy noted; **S-1 HARD GATE: kb-writer trio (65-of-81 dual-writes / 83.5% operational family / 7-18% reject rates) each re-derived + stamped (or dropped with disclosure) BEFORE the diff is presented — diff BLOCKED otherwise** |

**Actual origin — `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest`** verbatim:

> ADDENDUM-2 (leader via Ari 2026-11-23, job 71f7c48d) SUPERSEDES kb-writer rider in
> addendum-1. (1) RIDER RETRACTED - keep kb-writer pok grant; replace with ONE-FACT-ONE-STORE
> ROUTING CONTRACT in its prompts (operational→POK, knowledge→RAG, gate backstop); verify family
> 83.5% operational, reject rates 7-18% all authors.

**`85` / `85 rows` in a verify-family context: NOT FOUND.** Every `85`-near-`verify|family`
regex hit in R1/R2/R3 is unrelated (git shas, test counts, source line numbers). No file
states a verify-family size of 85 for kb-writer or for anyone.

**Does the 512-row JSON carry a verify-family count for kb-writer? NO.**
`/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/census-active-2026-11-23.json` holds MARGINALS only — `by_domain_active` and `by_created_by_agent_active`
are separate one-dimensional distributions; there is **no author × domain cross-tab**
(`specific_lookups` keys are only `views_origin_gate_contract` and `three_named_rows`;
`family_analysis` keys are `family_max`, `distinct_family_keys`, `families_over_max`,
`families_at_max`, `note`). The string `"verify"` appears 469× in the JSON, all of them
`domain` values / row-level domain fields — **never paired with an author count**.

⇒ **“85” and “83.5%” cannot be shown to describe the same population, and “85” cannot be
verified at all.** What IS measured:

| population | kb-writer rows | kb-writer × domain=verify | source |
|---|---:|---:|---|
| 512-row active (08:32Z) | 139 | **not in the artifact** (no cross-tab) | `census-active-2026-11-23.json` |
| 499-row active (12:29Z dump) | 131 | **79** | `census-rows-active-2026-11-23.mech-labels.tsv` |

So the relayed “85” is neither the 512-era kb-writer total (139) nor the 499-era verify-family
size (79). **It is unbacked.**

**Nearest in-branch SIBLING figure** (a different denominator — do not substitute):
`/home/nea/ensemble-src/.agents/shared/planning/pok-shape-review/architecture-recommendation-addendum-v2.md:34`:

> - **Class-admissible stock (measured): 325 lexical / ≈413 semantic** (82.8% operational, n=64 hand-label; lexical under-counts at 57% recall). The Emperor's boundary rejects **17–35%, not the bulk**.

⇒ **82.8% operational, n=64 hand-label** — a DIFFERENT number on a DIFFERENT denominator
(64 hand-labelled rows) than 83.5%.

#### Q2.c “gate-reject rates run 7-18% for every author (leader 8%)”

**In-branch FILE source: NOT FOUND.** `reject rate` / `reject-rate` / `reject_rate` → **0**
hits in R1 (excluding this file), R2 and R3. The `7-18` hits in R3 are date/line false
positives (`2026-07-18`, “rows 17-18”); the `83.5` hits in R2 are unrelated (pytest timing
“83.56s”, “p95_ms": 83.5”, “83.5% of process CPU”).

**Actual origin — `shared_meta_kv` `pok-write-gate.addendum-leader-2-digest`** (quoted above):
“**reject rates 7-18% all authors**”. The addendum carries **no method, no sample size, no**
**per-author breakdown, and no `leader 8%`** — the “leader 8%” variant appears in NO artifact
found (searched `leader 8%`, `8% reject`, `reject…leader`, `leader…reject` in R1/R2/R3).

**The decision brief with the SIMULATED gate-verdict method: NOT FOUND.** Exhaustive hunt:

- `find` for `*decision*` / `*brief*` files in R1/R2/R3 → no POK gate-verdict brief. The
  closest candidates are the architect’s `pok-shape-review/architecture-recommendation*.md`
  (4 files) — its “simulation” is an **admission** simulation, not a gate-verdict simulation:
  `architecture-recommendation-addendum.md:28`:

  > | A14 | Admission simulation over the census (deterministic fallback): **all 7 default views admit the IDENTICAL 8 rows** (union 8/499 = 1.6%) — because 378/499 are `both`, nothing pinned/curated/disputed. Lower bound: live query-driven fusion varies by instance | worker simulation |

  and `architecture-recommendation-addendum.md:95` (a shadow **target**, not a measurement):

  > | **P1 Instrument + shadow** | census read surface (curator-only product tool); gate SHADOW (verdicts logged, never refuses); `reason` soft-required | shadow coverage ≥95%; **shadow verdict mix ≈ census mix** (disagreement ⇒ rubric wrong — do not enforce) | **Yes** (kill-switch) |

- `find` for `*verdict*` / `*adjudicat*` / `*sem*label*` artifacts → **only** unrelated
  tester-gate verdict files (engine-pilot, views-session-bypass, maintenance-console); **no**
  POK gate verdict TSV/JSON.
- `grep` for `ROUTE-RAG` / `ADMIT…REJECT` / `verdict mix` → only `plan-overview.md` (the gate
  design prose) and `pok-shape-review/architecture-recommendation-addendum.md` (gate design prose).

⇒ **The per-author 7–18% / leader-8% numbers are NOT reproducible from any persisted
artifact**: there is no verdict TSV/JSON, no per-author table, and no described simulation
method anywhere in the searched roots. The numbers exist only in the relayed prompt
(leader addendum-2, relayed via Ari).

#### Q2.d Summary table

| figure | in-branch FILE source | actual origin | re-derivable? |
|---|---|---|---|
| (a) 65 of 81 dual-writes | **NOT FOUND** | shared_meta_kv `addendum-leader-1` | **NO** — 81-row class count has no per-row join key |
| (b) 83.5% operational verify family | **NOT FOUND** | shared_meta_kv `addendum-leader-2-digest` | **NO** as stated; my §B re-derivation (66/79 = 83.54%) is a NEW proxy-rule figure over a DIFFERENT population |
| (c) 7–18% reject, leader 8% | **NOT FOUND** | shared_meta_kv `addendum-leader-2-digest` | **NO** — no verdict artifact, no method, no per-author table |

---

### Q3 - THE 512-vs-499 RECONCILIATION

#### Q3.1 The 512-era census artifact

```
path   : /home/nea/ensemble-src/.agents/shared/planning/pok-sweep/census-active-2026-11-23.json
sha256 : d44d7d8b924ba62ad3ba1ad7302b02f79a340f8396448c010eff3d6f790ce199
mtime  : 2026-11-23T08:32:36.166361+00:00
size   : 879512 bytes
```

`generated_at: 2026-11-23T08:32:36Z`, `generated_by: census executor (read-only; no POK writes performed)`.

**Active count (verbatim from `counts`):** `active = 512` · `cap = 512` ·
`cap_gap_active_vs_cap = 0` · `headroom_slots = 0`.

**Per-author counts, `distributions.by_created_by_agent_active` (verbatim, 512 active rows):**

```
worker             154
kb-writer          139
leader             50
giter              27
coder              22
tester             22
developer          21
project-manager    17
devops             7
skill-keeper       7
maintenancer       6
reviewer           6
sonic-coder        5
approver           4
architect          4
planner            4
explorer           3
governor           3
sonic-developer    3
sonic-reviewer     2
sonic-tester       2
wanderer           2
kb-importer        1
tidier             1
```

`distributions.by_domain_active` (verbatim, same 512 set):

```
verify         298
artifact       61
environment    61
other          55
deploy         36
catalog        1
```

#### Q3.2 Which set was “448 operational / 26 general-knowledge / 38 rolling” computed over?

**The 512-row 08:32Z set** (not the 499-row set). Evidence:

- `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:31`:

  > | 08:32Z census | 512 / 667 | — | the set the 448/512 semantic pass classified |

- `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:50` (same line, quoted in full in §Q3.3):

  > The 08:32Z semantic pass counted **26 G + 38 R = 64** of 512 (12.5%). **Its per-row names were never persisted** (counts only in history 2bf04468) — so this lane RE-DERIVED the tail independently from the local TSVs with a strict, documented criterion, then reconciled:

- `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/phase12-plan.md:12`:

  > - **Snapshot-era CORRECTED (B1/B2 evidence fold, 2026-11-24): the previous attribution of the retrieval legs to the pok-sweep mechanics artifact was WRONG — those figures derive from `evidence-rederiv-2026-11-23/funnel-and-agreement.md` Figure 1 (2026-11-24); the relayed nested retrieval chain is DROPPED as unbacked (its figures are not reproduced here by design — see the fold ledger). Hand-label agreement figures derive from the same artifact (Figure 2, Bands B/C). Admissibility machine-parse figures (448/26/38 of 512) still derive from the PM pok-sweep census of the 512-active store (pre-sweep; UNVERIFIED in-branch — standing flag, not cured by this fold); class counts derive from this lane's census of the 499-active store @13:5xZ (post-sweep; differing class vocabulary, reconciled in the discrepancy note). Sources: evidence-pok-sweep/ (two TSVs + mechanics md, sha256-recorded) + evidence-rederiv-2026-11-23/funnel-and-agreement.md (sha256 b55101a3…8ada); exact per-figure timestamps are re-derived and stamped at Phase-12 guideline authoring before the diff is presented.**

- shared_meta_kv `pok-write-gate.addendum-leader-1`: “Measured overlap classes: BOTH-USEFUL
  337/512 (65.8%) / RAG-DUPLICATE 81 (15.8%) / POK-ONLY 78 (15.2%) / NEITHER 16 (3.1%)” and
  “MEASURED NUMBERS for guideline measured-state section: 512 active POK rows; 12,968-doc RAG
  corpus; the four overlap classes above; kb-writer same-run dual-write mechanism.”

⇒ the O/G/R split (448/26/38 = 512) and the overlap classes (337/81/78/16 = 512) are BOTH
computed over the **512-row 08:32Z set** — i.e. the PRE-sweep population. The 499-row
mech-labels TSV is a LATER, DIFFERENT population and carries no O/G/R label.

#### Q3.3 CONFIRMED: the O/G/R per-row names were NEVER persisted

`/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:50` (verbatim):

> The 08:32Z semantic pass counted **26 G + 38 R = 64** of 512 (12.5%). **Its per-row names were never persisted** (counts only in history 2bf04468) — so this lane RE-DERIVED the tail independently from the local TSVs with a strict, documented criterion, then reconciled:

and the R/G criteria that lane RE-DERIVED locally (`/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:52-53`):

> - **R (rolling):** the row's *operative claim* is only true relative to a movable NOW (version / staged-ness / tip sha / pid / host-disk state / dated counts). Dates/shas that are mere *provenance* for a durable rule do NOT qualify.
> - **G (knowledge-shaped):** the body states a fact that would hold in ANY repo (generic bash/git/pytest/python/PyInstaller discipline) with no ensemble binding.

⇒ **CONFIRMED.** The 448/26/38 split exists only as aggregate counts in history row
`2bf04468`; **no per-row O/G/R label was ever written**, so the O-vs-G split is **NOT**
measurable from any persisted artifact. Only the R-vs-non-R split is mechanically
measurable (via the `ROLLING:` label — Q1).

#### Q3.4 The 512 → 499 delta (why the two figures must not be mixed)

`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:50` and `:81`:

> PM's arithmetic: "512 active at 08:32Z − 12 archived + 2 added = 502."
> - **Net drift beyond PM's predicted -10:** PM predicted 499? PM predicted 502; measured 499 = -3 additional net removals. MEASURED. The 08:32Z→now delta is -13 active (512→499), not -10. INFERABLE: additional sweep archives (beyond the named 12) plus the 2 added account for the extra -3.

The 12 archived slugs are enumerated at `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:60-74`; the 2 added at `:78-80`; the
measured 499-active snapshot at `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:37`; and the TSV’s own snapshot caveat at
`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:12` (quoted in §Q1.8).

Also note the 512-era cap was **512** and has since been raised: `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:520` and `:528`:

> | POK_ACTIVE_CAP_PER_PROJECT | **1024** | 105 |
> - PM's suspicion VERIFIED: active cap was raised 512 → 1024 by v0.31.42 (the comment in source: "Raised to 1024 by the 2026-11-23 board commission wi-1921ba17 (sonic lane)"). The 08:32Z artifacts' "cap 512 / headroom 10" are STALE (they reflect v0.31.37).

---

### FOLLOW-UP LIMITATIONS

1. **`shared_meta_kv` was READ (not written)** to locate the trio’s true origin. The
   addendum rows are leader-authored digests relayed via Ari; they are KV values, not repo
   files, so the three figures have **no in-repo source** — only a relayed-prompt source.
2. **The “85 rows” figure cannot be falsified, only shown unbacked**: no artifact states it,
   and the 512 JSON carries no author×domain cross-tab that could produce it.
3. **`leader 8%` is the weakest link**: it appears in NO artifact found (not even in the
   addendum that carries “7-18% all authors”). It is a prompt-only variant.
4. **Q1 rules are era-pinned to the 499-era 12:29Z dump** (`/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/census-mechanics-2026-11-23.md:12`): the store
   grew to 501 active by 14:17Z that day, so re-applying to LIVE rows today requires a fresh
   dump — the rules are exact, the DATA is 5 days stale.
5. **My §B re-derivation (66/79 = 83.54%) is a proxy-rule figure over the 499-era set**, NOT
   a recovery of the 83.5% claim’s original (512-era, semantic-pass) computation. See §B.2 and
   §B.5 for the sensitivity band (77.22% – 83.54%).

