# Video Team Pre-Activation Test Plan — How We Prove the Team Before It Runs

**Deliverable:** C of A/B/C (A = architecture, B = agent definitions + data contracts — same branch, tonight)
**Status:** DESIGN — night-stage docs only. Zero daemon changes, zero existing-agent edits, nothing merged/pushed, no venv, no pytest, no lab execution.
**Base:** `feature/video-team-scaffold` @ `1132fa4cb` (= origin/latest at open)
**Author instance:** worker (video-team-night)
**Companion deliverables (same dir):** `architecture.md` (A), B's artifacts (the four `agents/video-*/` trees + `data/*.json` — B authored by the coder instance; A's header anticipated an `agent-definitions.md` summary that is not on disk; the artifacts themselves are B)
**Lab reference (read-only tonight):** `/Users/nguyenminhkha/All/Code/Yedda/video-lab/` + its `HANDOFF.md` (cited as HANDOFF §)

---

## 0. What this plan is

This is the verification methodology for the video team: **how every load-bearing claim in
A and B gets proven BEFORE activation**, not after the first mission fails. Nothing in
this document executes against any daemon tonight. Two kinds of content:

- **Tonight-run static checks** — actually executed tonight against the worktree's files
  (§6, results embedded inline, labeled `TONIGHT`).
- **Procedures** — the exact positive/negative cases, fixtures, commands, and expected
  outcomes for the armed lab (activation §12 steps 3/4/7/8) and for the user's morning
  review (§5). Every procedure names its fixture, its expected exit code, and the catch
  from v1-v5 it replays (A §0.2, HANDOFF §3/§5/§9).

**Contracts of record:** the seven `data/*.json` files are the machine-readable truth for
preflights, gate sets, profiles, schemas, and the invalidation rule. Where this plan and
a data file disagree, the data file wins and this plan is wrong.

| Data file (`.agents/shared/planning/video-team/data/`) | Governs |
|---|---|
| `preflights.json` | P1-P8 definitions, per-check cost caps, `exit_code_semantics` |
| `gate-sets.json` | `fast-demo-gates` / `film-goldens-gates` membership + per-gate mechanism + tolerances |
| `lane-profiles.json` | profile selection, caps (90 s), iteration caps (fast 3 / film 2), goldens/determinism flags |
| `shot-plan.schema.json` | the plan diff-review surface (A §5.3) |
| `action-log.schema.json` | the editor's append-only governance record (A §8.2) |
| `block-catalog.json` | the three v1 blocks + their gates (A §10) |
| `invalidation-spec.json` | `beat_valid(beat)` := five pinned shas (A §9) |

---

## 1. Preflight Self-Tests (P1-P8)

Every preflight is a script with a **stable exit code and a one-line reason** — no LLM
judgment, no vibes (A §5.1). `data/preflights.json` is the machine-readable source; the
scripts' `--help` output carries the same contract tables (A §5.4 — tools carry their own
contracts; dispatch prompts never re-explain them). Planned home: `video-lab/tools/preflight.mjs`
beside the harness it guards. **The script does not exist tonight** — it is authored at
**A §12 step 7a** (NEW, must run BEFORE step 7's self-test consumes it): write
`video-lab/tools/preflight.mjs` implementing P1-P8 per `data/preflights.json`, with a
`--help` contract per §5.4 (stable exit codes 0/1/2+). The executor is created in the
video-lab repo (NOT this repo) and is not on disk today (A §13 lists it as
"not yet on disk"; A §5.1 area marks it "NOT yet on disk"). Tonight §1 is the
acceptance spec the armed-lab step must satisfy; nothing here runs.

**Exit-code semantics (from `preflights.json`, tested verbatim):**

```
0   = pass
1   = named check failed (reason printed on one line to stdout)
2+  = harness fault (missing binary, unreadable input, crash) — NEVER interpreted as a product verdict
```

The 1-vs-2+ split is load-bearing: B's lanes treat a `1` as "stop, report, route around"
and a `2+` as "the harness itself is broken — anomaly candidate" (B:
`agents/video-capturer/tools_note.md`, `agents/video-verifier/tools_note.md` "Failure modes
I expect"). A self-test that never proves the `2+` arm invites misreading a broken probe
as a red product.

### 1.1 Per-preflight self-test spec

Cost caps are from `preflights.json` (`max_seconds`): P1-P6 ≤ 1 s, P7 ≤ 10 s sampled, P8 ≤ 1 s (stat-only).
Each spec: **positive** (known-good fixture → exit 0), **negative** (deliberately-wrong
fixture → exit 1 within the cap), **shape** (the command contract `--help` must print).

| ID | Check | Positive case (fixture → expect) | Negative case (fixture → expect, within cap) | Command shape (`--help` prints this) |
|---|---|---|---|---|
| P1 | Duration cap | `prod-v5/out/tabs-feature-16x9-v5-trim12.mp4` (55.0 s, HANDOFF §3) vs cap 90 → `P1 PASS duration=55.0s cap=90s`, exit 0 | `prod-v2/out/tabs-feature-16x9-v2.mp4` (83.0 s) vs a synthetic cap of 60 s → `P1 FAIL duration=83.0s > cap=60s`, exit 0 <1 s. IMPORTANT STATUS NOTE: HANDOFF's real cap is 90 s (HANDOFF §2, §6.8); the v2 83 s cut is OVER-TARGET, UNDER-CAP — the real 90 s cap does NOT fail it (83 < 90 → PASS). The synthetic cap=60 leg proves the P1 check FIRES; it is a self-test drill, not a claim that the 83 s cut breached anything. The preflight's in-HANDOFF anchor for the duration rationale is this 83 s over-target one-shot (HANDOFF §3 — verified; over-target, not over-cap). The 60,300-frame runaway cited in the kickoff brief is KICKOFF-BRIEF-SOURCED/UNVERIFIED against this lab (no retained artifact), so the synthetic over-target leg is the replay contract either way — the gate fires before any render spends the hours. | `preflight.mjs p1 --deliverable <mp4> --cap-s <n>` |
| P2 | Clip pinning | A v5 beat whose `inputs_sha` matches `build/captures/invalidation.json` ledger → exit 0 | A copy of a beat manifest with ONE hex digit of one `inputs_sha` flipped → `P2 FAIL stale sha <path> (ledger a3f…≠ input 9e2…)`, exit 0 <1 s. Also: a referenced beat clip deleted from `build/beats/` → exit 1 "missing" | `preflight.mjs p2 --plan <shot-plan.json> --tree <project root>` |
| P3 | Engines named | `prod-v5/provenance.json` (names OpenFilm pin + Chrome/engine + ffmpeg) → exit 0 | Same file with the engine-version field stripped → `P3 FAIL provenance missing engine version`, exit 0 <1 s (the v2 lesson: the engine drifted to Chrome 157 mid-run — HANDOFF §4) | `preflight.mjs p3 --provenance <provenance.json>` |
| P4 | Viewport floor | `prod-v5` capture meta (1920x1080) → exit 0 | `video-lab/dryrun/mobile-390x844.png` — width 390 < 1024 → `P4 FAIL viewport width 390 < 1024`, exit 0 <1 s. (The 390 px honesty artifact from HANDOFF §9 doubles as the standing P4 negative fixture.) | `preflight.mjs p4 --capture-meta <meta.json>` |
| P5 | Fixture-only tenants | v5 capture meta: base URL `http://127.0.0.1:8987/...`, tenant markers uid 9001 / tenant 1706 → exit 0 | (a) base URL rewritten to a non-localhost host (e.g. `http://10.0.0.1:8987`) → `P5 FAIL non-localhost base URL`, exit 0 <1 s, BEFORE any request is made; (b) markers rewritten to uid 9002 → `P5 FAIL non-fixture tenant uid 9002` | `preflight.mjs p5 --capture-meta <meta.json>` |
| P6 | Localhost-only DB | Lab stack env: DB host localhost, DB `ydlab_layout` → exit 0 | DB host env pointing at any non-localhost (e.g. `ydlab@db.internal`) → `P6 FAIL db host not localhost`, exit 0 <1 s, before any connection attempt | `preflight.mjs p6 --env <lab env file>` |
| P7 | Blank/luma scan (sampled) | v5 frame set (no uniform-luma stretch; the camera-clamp catch is what it hunts) → exit 0 | A synthetic 2 s uniform-gray stretch spliced into a copied frame dir → `P7 FAIL near-blank stretch frames 240-300 (luma σ<1)`, exit 0 ≤10 s. (Full-resolution scan is G-BLANK's job; P7 is the cheap sampled pre-assembly tripwire.) | `preflight.mjs p7 --frames <dir> [--sample-step N]` |
| P8 | Deliverable size | A real sub-32 MiB cut (e.g. `prod-v5/out/tabs-feature-16x9-v5-trim12.mp4`, 2,161,800 B) or a synthetic 31 MiB sparse file → `P8 PASS size=2161800 B < cap=33554432 B`, exit 0 | A synthetic deliverable AT or OVER the cap (32 MiB + 1 byte) → `P8 FAIL size=33554433 B >= cap=33554432 B (MAX_SERVED_BYTES — over-cap serves a uniform silent 404 at live_views.py:1279-1283)`, exit 1 <1 s. Cost cap ≤ 1 s (stat-only, no decode) | `preflight.mjs p8 --deliverable <mp4> [--cap-bytes 33554432]` |

**Fixture discipline for negatives:** every negative fixture is a COPY or SYNTHETIC —
no lab artifact is mutated. The two retained historical artifacts usable as negatives
tonight-shaped are the v2 83 s cut (P1) and `dryrun/mobile-390x844.png` (P4); the rest are
one-field tampered copies or synthetic frames, produced on the armed lab, never tonight.

### 1.2 The self-test harness itself

The preflight script carries a self-test mode that runs every leg of §1.1 and asserts
each lands its expected exit code **within its cost cap**:

```bash
node video-lab/tools/preflight.mjs selftest
# expected: one line per leg —
#   P1-pos ok (0.4s) | P1-neg ok (0.4s) | ... | P8-neg ok (0.1s)
#   SELFTEST 16/16 legs green  (8 preflights × pos/neg)
# exit 0; any leg wrong-code or over-threshold prints `LEG FAIL <id>-<pos|neg> ...` and exits 1
```

```bash
node video-lab/tools/preflight.mjs --help
# expected: the P1-P8 table (check / fails-when / max_seconds) + the exit-code
# semantics line, byte-equivalent in meaning to data/preflights.json —
#   assert: --help mentions P1..P8, "0 = pass", "2+" and "harness fault"
```

```bash
# harness-fault drill: prove the 2+ arm —
env PATH=/usr/bin:/bin bash -c 'node video-lab/tools/preflight.mjs p1 --deliverable /nonexistent.mp4 --cap-s 90'
# expected: exit 2 (or 3) with a harness-fault line — NEVER exit 1, never a product verdict
```

Acceptance: **selftest 16/16 (8 preflights × pos/neg) + `--help` contract match + fault-drill exits 2+** is the
gate A §12 step 7 runs against the v5 known-good cut before any lane is dispatched.
Tonight NONE of this runs (no engine execution in the lab) — this section is the
acceptance spec the armed-lab step must satisfy.

---

## 2. Gate Dry-Runs on Fixture Captures (v1-v5)

The two gate sets (`data/gate-sets.json`) are proven against the five delivered cuts
BEFORE they guard new work: **each gate must re-catch what it historically caught**
(A §0.2 is the traceability record). Tonight this is a dry-run SPEC — fixtures named,
expectations stated, nothing executed in the lab. On the armed lab these become
read-only file probes (ffprobe/atom-walk/frame-hash over retained artifacts) plus
procedural replays.

### 2.1 Fixture inventory (verified on disk tonight, read-only `ls`)

| Fixture (under `/Users/nguyenminhkha/All/Code/Yedda/video-lab/`) | Facts (HANDOFF §3/§9) | Dry-run role |
|---|---|---|
| `prod/out/tabs-feature-16x9.mp4` | v1, 44.7 s, 9 beats | G-SPEC/G-FASTSTART positive; G-PLAN era check |
| `prod-v2/out/tabs-feature-16x9-v2.mp4` | v2, 83 s, one-shot | **P1 negative fixture (SYNTHETIC cap=60 only — the real 90 s cap does NOT fail it, since 83 < 90)**; G-POINTER calibration source (max dev 0.0044 px, HANDOFF §5) |
| `prod-v3/out/tabs-feature-16x9-v3.mp4` | v3, 67.0 s, 2010 frames, 2.37 MB, all-intra footage | G-SPEC positive; summary-gap fix evidence (instant-summary panel, A §10) |
| `prod-v4/out/tabs-feature-16x9-v4.mp4` | v4 cut (present; not described in HANDOFF §3's primary list) | incidental G-SPEC probe only |
| `prod-v5/out/tabs-feature-16x9-v5.mp4` | v5 full cut, 67.0 s, 2010 frames, 2,468,574 B | **G-3LAUNCH / G-DETERM source cut**; golden-mint source |
| `prod-v5/out/tabs-feature-16x9-v5-trim12.mp4` | **v5 PRIMARY cut, 55.0 s, 1650 frames, 2,161,800 B — a stream copy of frames 361..2010** | P1 positive; **stream-copy exactness replay** (1650/1650, 0 mismatches vs the full cut's range); G-SPEC/G-FASTSTART positive |
| `prod-v5/capture/`, `capture-recap/`, `capture-recap2/` | three independent capture launches (frame PNG sets) — historically all three hash-identical 2010/2010 | **G-3LAUNCH replay fixture** |
| `prod-v5/provenance.json` + `PROVENANCE.md` + `REPORT.md`; same tier in prod/, prod-v2/, prod-v3/ | the machine-readable verification chains | P3 positive fixtures; fe_build_sha pin (v5 proven at `47cecde1` with the uncommitted layout-server flip — HANDOFF §9) |
| `prod/review/verify-report.json`, `prod-v2/`, `prod-v3/`, `prod-v5/` (same shape) | prior gate verdicts | baseline for "the new gate set must reproduce the old verdicts" |
| `prod/preview.html`, `prod-v2/preview.html` | self-contained previews (mp4 + contact sheet base64-inlined, zero external URLs — HANDOFF §3) | §4(a) static shape evidence (checked tonight — §6) |
| `dryrun/desktop-1920x1080.png`, `dryrun/mobile-390x844*.png` | viewport probes; the 390 px honesty artifact | P4 negative (390 px); aspect evidence |
| `tools/capture.mjs` + `lab-lib.mjs` (+ `density-shots.mjs`, `redaction-audit.mjs`, `finalize-determinism.mjs`) | the deterministic capture harness (per-frame control, per-frame coord logs) | `harness_sha` pin source; G-POINTER evidence producer |

### 2.2 What each gate asserts — dry-run expectation matrix

Gate membership and tolerances are from `gate-sets.json` / `lane-profiles.json`
(fast-demo = **5 gates**: G-PLAN, G-BLANK, G-FASTSTART, G-SPEC, G-EYES; film+goldens =
**10 gates**: the same five plus G-GEO ≤ 2 px, G-POINTER ≤ 0.005 px, G-DETERM, G-3LAUNCH,
G-GOLDEN; G-DETERM is the canonical name — no space, no "M" suffix).

| Gate | Asserts | Dry-run fixture + expected verdict | Replays (A §0.2) |
|---|---|---|---|
| G-PLAN | performed-timeline (capture meta) == authored plan, any drift fails | `prod-v5` performed timeline vs its plan → PASS (the plan==performed reconciliation landed v3→v5; HANDOFF §9). Deliberate-drift negative: an edited plan copy (one `at_frame` shifted) against the same meta → FAIL naming the beat | plan == performed catch |
| G-BLANK | full-res luma-uniformity scan, no dead frames ship | `prod-v5-trim12` → PASS. Negative: the synthetic uniform stretch from §1.1 P7 at full res → FAIL | camera clamp / blank-frame catch |
| G-FASTSTART | moov precedes mdat (atom walk or ffprobe trace) | all five cuts → PASS (OpenFilm emits faststart-ordered — verify, don't remux: HANDOFF §4). Negative: a byte-swapped-atom copy (`mdat` first) → FAIL | streamability requirement |
| G-SPEC | h264 / yuv420p / 1920x1080 / fps / duration ±1 frame of plan | v3 (67.0 s/2010 f) + v5-trim12 (55.0 s/1650 f) → PASS. Negative: v2 (83 s one-shot) judged against a 90 s plan with duration mismatch injected → FAIL on duration | spec-conformance requirement |
| G-EYES | contact sheet + keyframe stills READ; verdict names what it saw | procedural (B: `agents/video-verifier/skills-template/video-gates.md` protocol) — tonight's honest state: **zero eyes have ever watched v1-v5** (HANDOFF §9); the dry-run protocol closes this on the first gated mission | the vision-channel requirement |
| G-GEO (film) | per-frame logged bboxes vs plan positions, ≤ 2 px | `prod-v5` beats with logged geometry vs plan → PASS at ≤ 2 px. Negative: a plan copy with one bbox shifted 5 px → FAIL with measured delta | geometry-measurement catch |
| G-POINTER (film) | pointer overlay coords vs harness per-frame input log, ≤ 0.005 px, zoom windows included | `prod-v2` → PASS **by calibration**: measured max deviation 0.0044 px < 0.005 (HANDOFF §5) — the tolerance is set just above the worst real measurement, which is exactly what the dry-run must confirm. Negative: a synthetic 0.01 px offset → FAIL | pointer fidelity in zoom windows |
| G-DETERM (film) | per-frame SHA-256 across TWO independent renders — never the engine's 12-frame `look` sample (HANDOFF §4) | the v5 launch pair (capture vs capture-recap) at frame level → PASS 2010/2010 (historical). Negative: the **range-render replay** below → FAIL | CSS-alpha nondeterminism + full-frame proof discipline |
| G-3LAUNCH (film) | three independent launches agree hash-for-hash | `prod-v5/capture` + `capture-recap` + `capture-recap2` → PASS (historical: all three byte-identical, `all_pairs_pass`) | 3-launch re-capture catch |
| G-GOLDEN (film) | tier-aware golden regression (ONE gate id; fast = goldens OFF; film = hybrid E+P+R dispatch) — E: masked-region per-frame hash (zero tolerance); P: SSIM/dhash vs per-beat stored thresholds; R: scene-boundary + endpoint coverage; **S: per-keyframe written verdict (evidence for human approval, not auto pass/fail)** | first film iteration = **MINT, not compare** (no goldens exist yet — §3); at mint the beat's `comparison` + `comparison_thresholds` are captured into provenance (incl. adaptive sampling config: action windows from the action log, SSIM-drop boundary detector, 30-frame baseline interval); from the second render on: PASS expected per tier (E loud regression / P measured score vs threshold + first flagged frame / R coverage gap / S verdict recorded); negative: a golden compared against a wrong-`fe_build_sha` render → FAIL with the tier-appropriate evidence; adaptive-sampling assertion pointer → §3.9 | no-silent-quality-slide requirement |

### 2.3 Replay cases — the gates must re-catch their historical catches

| # | Historical catch | Replay procedure (armed lab; nothing tonight) | Expected gate outcome |
|---|---|---|---|
| R1 | Duration cap breach — the in-HANDOFF anchor is an unpinned-clip 83 s over-TARGET (under the 90 s cap) v2 cut (HANDOFF §3, verified); a 60,300-frame runaway is KICKOFF-BRIEF-SOURCED/UNVERIFIED (no retained artifact) | P1 negative leg (§1.1) + G-SPEC duration leg against a synthetic over-target fixture | P1 exit 1 < 1 s; G-SPEC FAIL on duration — the runaway class is caught BEFORE assemble/render, in seconds |
| R2 | Camera clamp shipped dead frames (found by blank-frame scan) | P7 sampled negative + G-BLANK full-res negative (synthetic uniform stretch; and, if the clamp stretch is identifiable in a retained v1/v2 capture dir, the real frames) | both FAIL naming the frame range |
| R3 | CSS-alpha nondeterminism (the xfade ruling, A §7.4) | Render one transition two ways on the armed lab: (a) browser alpha compositing, (b) ffmpeg `xfade` on decoded segments; run G-DETERM on each | (a) FAILS frame-hash agreement across renders; (b) PASSES — proving the ban is earned, not dogma |
| R4 | Range-render nondeterminism (v5: 1245/1650 frames wrong — HANDOFF §9) | Re-run the v5 range render once; G-DETERM it against the retained `capture/` frame set; THEN stream-copy trim `frames 361..2010` from the all-intra full cut and hash-compare against `tabs-feature-16x9-v5-trim12.mp4`'s frame set | range render FAILS (historically 1245/1650 diverged); stream copy PASSES hash-for-hash (historically 1650/1650, 0 mismatches) — proving A §7.2's ban and the all-intra prerequisite in one drill |
| R5 | plan == performed | G-PLAN positive + deliberate-drift negative on `prod-v5` (§2.2 row 1) | PASS on the real pair; FAIL on the shifted copy |
| R6 | Geometry measurement | G-GEO on `prod-v5` logged bboxes + 5 px-shifted negative | PASS ≤ 2 px; negative FAILs with delta |
| R7 | Pointer fidelity in zoom windows | G-POINTER on `prod-v2` (0.0044 px) + synthetic 0.01 px offset | PASS at tolerance; negative FAILs — tolerance proven calibrated, not decorative |
| R8 | 3-launch re-capture | G-3LAUNCH over the three retained v5 launch dirs; plus the film-profile rule that a `harness_sha` bump forces a fresh 3-launch (`invalidation-spec.json`) | PASS 2010/2010 ×3 pairs; the bump rule acknowledged in the verdict |

**Expected per-profile outcome on the v5 primary cut** (`fast-demo` treatment): P1-P8 +
G-PLAN + G-BLANK + G-FASTSTART + G-SPEC + G-EYES all green → a fast-demo lane can treat
v5 as the known-good base (A §12 step 7). Under `film+goldens`: additionally G-GEO,
G-POINTER, G-DETERM, G-3LAUNCH green; G-GOLDEN is a first-mint (no golden exists yet) —
at mint the beat's `comparison` + `comparison_thresholds` (incl. adaptive sampling
config and Tier S checklist) are captured into provenance; regression semantics
(E/P/R tier-aware + Tier S verdict) begin at the second render of the same beat (§3).

### 2.4 Read-only artifact probes (the dry-run's cheapest tier — runnable any morning)

These probe RETAINED artifacts only: no stack start, no render, no engine. They are the
first thing the armed lab runs (and the user may run them any time — they touch nothing):

```bash
L=/Users/nguyenminhkha/All/Code/Yedda/video-lab
ffprobe -v error -show_entries format=duration -of csv=p=0 "$L/prod-v5/out/tabs-feature-16x9-v5-trim12.mp4"
# expect: ≈55.0   (P1 positive leg, G-SPEC duration anchor)
ffprobe -v error -show_entries format=duration -of csv=p=0 "$L/prod-v2/out/tabs-feature-16x9-v2.mp4"
# expect: ≈83.0   (the P1 negative fixture's real duration)
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,pix_fmt,width,height,nb_frames \
  -of csv=p=0 "$L/prod-v5/out/tabs-feature-16x9-v5-trim12.mp4"
# expect: h264,yuv420p,1920,1080,1650   (G-SPEC conform)
shasum -a 256 "$L/prod-v5/capture/frame-00001.png" "$L/prod-v5/capture-recap/frame-00001.png" "$L/prod-v5/capture-recap2/frame-00001.png"
# expect: three IDENTICAL hashes (G-3LAUNCH spot sample — the full gate hashes all 2010)
```

The moov-before-mdat atom walk and the full per-frame hash compares are scripted gates
(G-FASTSTART / G-DETERM), not one-liners; the probes above are the human-auditable
sample of the same evidence.

---

## 3. Golden-File Regression Procedure (film profile only)

Requirement 6 (A §0.1, §6.3): determinism CI with committed goldens. Shape per
`gate-sets.json` G-GOLDEN (tier-aware, one gate id); storage per **OQ3 — RESOLVED BY
USER RULING (2026-11-12): Option A** (lab binaries + in-repo sha256 manifests).
The winning model is the **three-tier hybrid (E + P + R)**; per-frame-SHA-everywhere
was rejected.

### 3.1 What a golden is

```
goldens/<beat-id>/           # inside the project tree (A §8.1); Option A (approved):
├── clip.mp4                 #   binaries live in video-lab, manifests in-repo
├── manifest.sha256          # per-frame sha256 list, frame-count-prefixed
└── provenance.json          # engine versions, fixture sha, fe_build_sha, harness_sha,
                             #   lab_stack_sha, AND the per-beat comparison config
                             #   (comparison + comparison_thresholds — see §3.3)
```

In-repo mirror (subject repo project tree, A §8.1 layout `goldens/<beat-id>/`): the
`manifest.sha256` + `provenance.json` ONLY (a few KB per beat) — the ~2-8 MB binaries
stay in the lab at the mirrored path. The manifest is the determinism evidence; the lab
copy is the comparison target. The path shape is `goldens/<beat-id>/` everywhere —
inside the `<project-id>` tree (A §6.3/§8.1, `gate-sets.json` G-GOLDEN), never a
`<project>` directory layer.

### 3.2 The three tiers (E / P / R)

| Tier | What it compares | Default thresholds | Where configured |
|---|---|---|---|
| **E — exact** | masked-region per-frame hashes over deterministic regions (text / indicator pills / overlay chrome / controlled UI); whole-frame when the beat is fully deterministic | **zero tolerance** — byte-exact frame hash | per-beat `comparison` + `comparison_thresholds` in the shot plan; stored in golden provenance at mint |
| **P — perceptual** | generated/AI footage + legitimate render variance | **film defaults: `ssim_min = 0.97`, `dhash_hamming_max = 6` at 64-bit** (per-beat configurable) | per-beat `comparison_thresholds` in the shot plan; stored in golden provenance at mint |
| **R — representative** | scene boundaries + each beat's first/last frame + action-window frames (pointer travel / press / drag / drop / popup open / row edits) + uniform baseline between them, long edge ≤ 480 p | **adaptive** (default — dense around scene boundaries via SSIM-drop/frame-diff + forced sample points at action windows + uniform baseline interval between); modes: `scene` | `every_n` | `adaptive` | per-beat `comparison_thresholds.representative` in the shot plan; stored in golden provenance at mint |

**P-metric semantics: AND by default.** When both `ssim_min` AND `dhash_hamming_max`
are configured for a beat, BOTH must pass (SSIM mean ≥ ssim_min AND dHash Hamming ≤
dhash_hamming_max); passing one does not excuse the other.

**Lane defaults:** `fast-demo` = goldens OFF (unchanged); `film+goldens` = **hybrid
(E + P + R)** — G-GOLDEN is ONE gate id, membership unchanged (fast 5 / film 10);
it becomes tier-aware internally, dispatching each beat to the tier its `comparison`
field declares.

**Tier S (semantic) is the EVIDENCE LAYER, not a comparison threshold.** It rides
alongside the hybrid E+P+R model: per-keyframe written verdicts (must_include /
must_exclude) recorded in the verify report as evidence for human approval. It does
NOT add a fourth comparison threshold and does NOT auto-pass/fail; a miss is surfaced
loudly. See §3.10.

### 3.3 Per-beat threshold-at-mint rule (REQUIRED fields)

Every beat carries, at mint time, the exact shot-plan fields (stored in golden
provenance — never inferred at compare time; the MINT-CAPTURE set is frozen in full
per invalidation-spec.json's MINT-CAPTURE guard):

- `comparison`: `exact` | `perceptual` | `representative` | `hybrid`
- `comparison_thresholds`: `{"ssim_min", "dhash_hamming_max", "representative": {"mode": "adaptive" | "scene" | "every_n", "n", "boundary_detector": {"metric": "ssim_drop" | "frame_diff", "threshold"}, "action_windows": {"source": "capture_action_log", "types": [...], "pad_frames": 3}, "baseline_interval_frames": 30}}`
- `masks[]`: `{mask_id, sha256, regions, beat_id}` — versioned mask artifacts
- `mask_area_budget_pct`: default 30 (masked-out area ≤ this % of frame area)
- `critical_regions`: the declared critical regions the exact tier MUST cover

A threshold change or a re-mint is **APPROVAL-WORTHY** — recorded in provenance
(reason + approver), never silent. No per-run threshold tuning: the thresholds live
in the shot plan and are frozen into the golden at mint.

**`boundary_detector.threshold` derivation:** starts from the seed/`null` (contract
default), is DERIVED at calibration alongside the perceptual thresholds (the detector
threshold that separates scene boundaries in the approved sample set), and is frozen
into the golden at mint like every other threshold; changes after mint are
APPROVAL-WORTHY like any other threshold delta.

**Single comparison per render:** exactly ONE `comparison` value governs a beat's
golden comparison per render. `hybrid` means E + P + R are all compared under that one
value, with S as the evidence layer alongside — it never means "two comparison values
race". Per-beat opt-out of Tier S on the film lane is APPROVAL-WORTHY (the film-lane
default is Tier S ON).

### 3.4 Minting (first green cut per beat, film profile)

1. The beat's render passes the full film gate set (§2.2) including G-3LAUNCH.
2. The verifier reads the beat's `comparison` + `comparison_thresholds` from the shot
   plan and hashes the winning render per the declared tier (E: masked-region per-frame
   hashes; P: representative-frame SSIM/dhash; R: scene-boundary + endpoint frames).
3. The verifier copies the clip + manifest + provenance to the golden dir (lab) and the
   manifest + provenance to the in-repo mirror; commits the mirror.
4. The mint is RECORDED in the verify report: `golden: minted (generation 1)` with the
   per-beat comparison config captured in provenance.

A golden is never minted from a render that skipped a gate; minting is the verifier's
act (B: `agents/video-verifier/skills-template/video-gates.md`), never the editor's.

### 3.5 Comparison semantics (tier-specific)

| Tier | Comparison | Mismatch evidence |
|---|---|---|
| E (exact) | **loud regression** — masked-region per-frame hash diff | names the **first divergent masked-region frame**; whole-frame hash when the beat is fully deterministic |
| P (perceptual) | measured SSIM/dhash score vs the stored threshold | reports the measured score vs threshold **+ the first flagged representative frame** |
| R (representative) | coverage — scene boundaries + endpoints present | reports the **coverage gap** (missing scene/endpoint frames), NOT a pixel verdict |

**E-tier mismatch** is a loud regression: the verify report FAILS G-GOLDEN and the lane
STOPS at the cap-and-flag boundary (A §6.4). Never averaged, never fuzzed, never
"retry until green" (B verifier Cardinal: retry-until-green is falsification).

**P-tier mismatch** names the measured SSIM/dhash score against the stored threshold and
the first flagged representative frame — the threshold is the per-beat minted value,
not a per-run override.

**R-tier mismatch** is a coverage gap: missing scene-boundary or endpoint frames, not a
pixel verdict — it says "the representative set is incomplete", never "the pixels differ".

### 3.6 What a mismatch MEANS

- Who may loosen, tighten, or retire a threshold/golden: **only the user or leader**
  (A §6.4; B producer Cardinal #6). The team's remedies are DISCLOSED PROPOSALS —
  e.g. "the golden predates the approved plan delta; re-mint generation 2" — never
  silent actions.
- **Threshold change or re-mint is APPROVAL-WORTHY** — recorded in provenance (reason
  + approver), never silent.
- **Missing golden = BLOCKED-WITH-A-PATH**: the verifier proposes a re-mint with a reason
  class from the full 5-value enum `{lab_binary_lost, approved_plan_delta, threshold_change, calibration_update, mask_change}` → human approves
  → generation N+1 is recorded with the approval reference. The cut is blocked until then;
  it does not silently pass.
- Diagnostic order on mismatch: (1) read `provenance.json` of both sides — engine
  version drift? (the Chrome 157 lesson, P3) (2) `fe_build_sha` / `fixture_sha` drift?
  (invalidation territory, A §9 — then this is a RE-CAPTURE case, not a regression)
  (3) genuinely same-input different-frames → nondeterminism → G-DETERM investigation.

### 3.7 Golden lifecycle

| Event | Golden action |
|---|---|
| First film-profile green cut of a beat | mint generation 1 (§3.4) with the per-beat comparison config captured in provenance |
| User-approved plan delta changing that beat's pixels | mint generation N+1 after its own full-gate pass; generation N retained (lab-side) and the supersede recorded in provenance |
| `harness_sha` or `lab_stack_sha` bump | ALL goldens re-minted after a fresh G-3LAUNCH green (invalidation-spec: harness/stack changes invalidate everything) |
| `fe_build_sha` change on untouched routes | NO re-mint — the golden's provenance records the build it pins; beats whose route changed are re-captured first (A §9), re-gated, then re-minted if pixels changed |
| Threshold change (approval-worthy) | re-mint generation N+1 with the new `comparison_thresholds`, approval reference recorded in provenance |
| Golden binary lost from the lab | BLOCKED-WITH-A-PATH: verifier proposes re-mint with reason class `lab_binary_lost` → human approves → generation N+1 (OQ3 resolved — no silent pass) |
| Mask change (approval-worthy) | re-mint / re-validate the affected beat(s) with reason class `mask_change`; provenance records the new `{mask_id, sha256, regions, beat_id}` and the approval reference |
| Calibration update (approval-worthy) | threshold re-derivation from a NEW approved sample set (≥5 renders); provenance records sample ids / distribution stats / derived thresholds; loosening beyond the seed floor requires this class explicitly |

---

### 3.8 CI shape (post-activation)

Re-render each golden beat on demand (film profile missions) → tier-aware diff vs
manifest → verdict line per beat (E: frame-hash diff; P: score vs threshold + first
flagged frame; R: coverage check). There is no hosted CI runner in this design; "CI" is
the verifier's standing procedure on the armed lab — the in-repo manifests make the
evidence REVIEWABLE (and portable) without committing binaries. Option A (lab binaries +
in-repo manifests) is **APPROVED BY USER RULING (2026-11-12)**; the thresholds are
per-beat-at-mint and frozen into provenance, so a remote CI can re-run the same
comparison against the same manifest.

### 3.9 Adaptive sampling (Tier R) — demonstration

**Assertion (SET-CONTAINMENT + COVERAGE-GAP):** for a fixture beat, the sample set S
must satisfy (a) every action-window frame ± `pad_frames` (3) from the capture
harness record (`action_windows.source` = `capture_action_log` — the RENAMED field;
the capture record is `video-lab/prod-v5/capture/inputs.json`: 16 sections with ids
like `p1-open`/`p1-focus`/`p1-approach`/`p1-press`, frames[] carrying per-frame
`pointer` state, events[] carrying `{seq, frame, type, x, y}` (316 events in the
v5 record, verified by `python3 -c "import json;print(len(json.load(open('.../inputs.json'))['events']))"`); `action_type_map` maps event type → section id so windows are mechanically derivable — this map is DECLARED BY CONTRACT (it must be emitted by the capturer at activation; it is NOT yet present in the v5 record, `grep -rn action_type_map` over the lab = 0 hits)
(types: pointer travel / press / drag / drop / popup open / row edits) is in S, and
(b) boundary-adjacent frames from the SSIM-drop/frame-diff detector are in S, with
uniform baseline coverage between them (`baseline_interval_frames` = 30, default). The
coverage gap between any two consecutive samples ≤ `baseline_interval_frames`.

**Expected output shape (per-beat sampling manifest):**

```
{
  "beat_id": "popup-session",
  "required_points": [12, 45, 46, 47, 48, 49, 50, 51, 52, 180, 181, 182, 210, 211, 212],
  "sampled":      [0, 12, 30, 45, 46, 47, 48, 49, 50, 51, 52, 60, 90, 120, 150, 180, 181, 182, 210, 211, 212],
  "gaps":         [12, 18, 15, 1, 1, 1, 1, 1, 1, 1, 8, 30, 30, 30, 30, 1, 1, 28, 1, 1],
  "max_gap": 30,
  "verdict": "PASS"   // max_gap <= baseline_interval_frames (30) AND all required_points ⊆ sampled
}
```

FAIL shape: `"verdict": "FAIL", "missing_required": [47], "max_gap": 45 (> 30)`.

The contract's `representative.mode` is `adaptive` by default (every_n/scene are
fallback modes). A uniform every-N default is a bug — sweep any surviving uniform-every-N
or global-threshold language before shipping.

**NAME-COLLISION TRAP (disambiguation):** `action-log.schema.json` in this scaffold is
the EDITOR governance log (action kinds trim / text-swap / zoom-crop / …) — it is NOT
the capture harness input record. The capture-side sampling source is the harness
record (`capture_action_log`, `video-lab/prod-v5/capture/inputs.json`); the editor's
append-only log is a different artifact consumed by different gates. The former field
name `action_log` collided with the editor log — the rename to `capture_action_log`
is the fix.

### 3.10 Tier S per-keyframe verdict (evidence, not a threshold)

Tier S (semantic) is the **evidence layer**, not a comparison threshold — it rides
alongside hybrid E+P+R. Per-keyframe written verdict (must_include / must_exclude)
recorded in the verify report as evidence for human approval. `evidence_only: true` in
the contract (gate-sets.json G-GOLDEN tiers.semantic): on-miss = SURFACED LOUDLY,
never buried; per_keyframe_verdict = true; checklist_source = shot-plan per-beat.

**Worked verdict table (illustrative plan-data against a real v5 fixture frame — the
caption-side G10 case, HANDOFF §9: the G10 gate labelled the caption `right` while the
pixels said `LEFT`; the caption's panel at x 650..856, teal accent at x 668..671, the
button's box at x 889.6..1030.4, 33.6 px clear on the left):**

| Keyframe | must_include | must_exclude | Verdict | Written note |
|---|---|---|---|---|
| result-landed frame (instant-summary panel) | subject tab, expected popup rows, colour swatch | hidden tab | **PASS** | "subject tab visible; popup rows match plan; swatch present; no hidden tab" |
| popup-open frame | UI Options button, centred tab group | (none) | **MISS** | "UI Options button present but caption labelled `right` while the button sits at x 889.6..1030.4 (33.6 px clear on the left of the centred group) — surfacing for human review" |

The MISS row is the surfacing shape: a written verdict naming what was seen, not a
boolean. Tier S output is EVIDENCE FOR HUMAN APPROVAL — never auto pass/fail.

**Delivery obligation:** every Tier S miss must be ACKNOWLEDGED (accept / re-capture /
re-scope) before the cut may be delivered — a miss blocks delivery until acknowledged,
never buried.

**Keyframe bound: ≤ 3 keyframes per beat** for Tier S vision calls. The bound keeps
the vision-call fan-out bounded and load-bearing (see §3.13's bounded/unbounded
arithmetic).

### 3.11 Calibration from approved samples

**Procedure:** collect ≥5 approved renders → per-beat per-metric distribution →
threshold = mean − 3σ → record `calibration_sample_ids` / `calibration_stats` /
`derived_thresholds` in provenance. With fewer than 5 samples: **fail-closed defaults**
apply — `ssim_min = 0.995`, `dhash_hamming_max = 2` — which are STRICTER than the
lane defaults (0.97 / 6), never looser. Threshold change or re-mint is
APPROVAL-WORTHY (reason + approver recorded).

**Worked example (ONE real beat — procedure, not measurement):** v5 full cut
(`prod-v5/out/tabs-feature-16x9-v5.mp4`, 2,468,574 B, 2010 frames, 67.0 s) is the
golden-mint candidate. We cannot run ffmpeg/SSIM tonight, so the procedure is recorded
here; the numbers land at activation step 10 (film/goldens verification):

```
# inputs (MEASURED from disk tonight):
#   capture launches: 3 (capture/, capture-recap/, capture-recap2/) — all 2010/2010 byte-identical (HANDOFF §9)
#   frames per launch: 2010 (ls | wc -l, read-only)
# command shape (activation day, against the v5 known-good cut):
  python3 calibrate.py --beat <beat-id> --renders capture/ capture-recap/ capture-recap2/ \
    --metric ssim --metric dhash_hamming --representative-frames scene_boundaries+endpoints \
    --k 3 --min-samples 5
```

**Row (a) — n=3 (BELOW the 5-sample minimum): fail-closed.** The sample stats may be
recorded for traceability, but `derived_thresholds` is NULL and the UNCALIBRATED
FALLBACK (0.995 / 2) GOVERNS — the looser lane seeds (0.97 / 6) are NEVER displayed
as governing:

```json
{
  "calibration_sample_ids": ["capture", "capture-recap", "capture-recap2"],
  "calibration_stats": {"ssim": {"mean": 0.982, "sigma": 0.004, "n": 3}, "dhash_hamming": {"mean": 1.2, "sigma": 0.8, "n": 3}},
  "derived_thresholds": null,
  "governing": "uncalibrated_fallback",
  "active_thresholds": {"ssim_min": 0.995, "dhash_hamming_max": 2}
}
```

**Row (b) — n≥5 (derived, seed-floor CLAMP applied).** Worked arithmetic with sample
stats where mean−3σ lands at-or-stricter than the seeds after integer flooring:

```json
{
  "calibration_sample_ids": ["r1", "r2", "r3", "r4", "r5"],
  "calibration_stats": {"ssim": {"mean": 0.981, "sigma": 0.002, "n": 5}, "dhash_hamming": {"mean": 2.0, "sigma": 0.5, "n": 5}},
  "derived_thresholds": {"ssim_min": 0.975, "dhash_hamming_max": 2},
  "governing": "derived",
  "active_thresholds": {"ssim_min": 0.975, "dhash_hamming_max": 2}
}
```

Arithmetic: ssim derived = 0.981 − 3×0.002 = 0.975 (≥ 0.97 seed floor — clamp passes);
dhash derived = 2.0 − 3×0.5 = 0.5 → integer flooring to 1 (≤ 6 seed floor — clamp
passes, and 1 is STRICTER than the seed 6, which is allowed). A derived value that
would be LOOSER than the seed floor (ssim < 0.97 or dhash > 6) is rejected by the
`seed_floor_clamp` (gate-sets.json G-GOLDEN tiers.perceptual.calibration); loosening
beyond the seeds requires an approval-worthy `calibration_update` event.

**σ=0 case:** if a metric's sample distribution has σ = 0 (zero-variance sample set),
record `"sigma_zero": true` and treat that metric as EXACT-MATCH at its mean (any
deviation from the observed mean is a regression) — per the contract's
`sigma_zero_policy`. LABEL: PROCEDURE-NOT-MEASUREMENT — the formula, clamp, and output
shapes are specified; the numbers are illustrative until activation step 10 runs.

### 3.12 Mask validation — four negative cases

Masks are versioned artifacts: provenance records `{mask_id, sha256, regions, beat_id}`.
Mask change is APPROVAL-WORTHY with reason class `mask_change`. Three mandatory checks
(invalidation-spec.json guards): (a) every declared critical region inside the
exact-compare area — exclusion = FAIL; (b) masked-out area ≤ `mask_area_budget_pct`
(default 30) — exceedance = FAIL; (c) mask change APPROVAL-WORTHY.

**Negative case 1 — over-broad mask (area budget breach):** a mask blanketing
1280×720 px of a 1920×1080 frame. Masked-out AREA = 1280 × 720 = 921,600 px²; frame
AREA = 1920 × 1080 = 2,073,600 px²; ratio = 921,600 ÷ 2,073,600 = 0.4444 = **44.4%**
> 30% budget → **validation FAIL**, measured percentage vs budget reported. (Trap
noted: 66.7% is the per-DIMENSION ratio — 1280/1920 × 720/1080 each = 0.667 — the
AREA ratio compounds: 0.667 × 0.667 = 0.444. Only the area figure is compared to the
budget.) The abuse path (mask away the hard parts) is closed by the budget.

**Negative case 2 — mask excluding a declared critical region:** a mask that covers the
overlay chrome area but excludes the subject tab's bounding box (a declared critical
region from the beat's plan geometry). Subset assertion FAILS: masks must be a SUBSET
of declared non-critical regions; excluding a critical region means the exact tier
cannot verify it. Concrete geometry (v1-v5 fixture frame, 1920×1080): subject tab
bbox [x: 889.6, y: 0, w: 140.8, h: 48] (the UI Options button region from HANDOFF §3
WARNING); mask region [x: 0, y: 0, w: 1920, h: 1080] claims to mask it but the critical
region is inside → subset assertion FAIL.

**Negative case 3 — 100% bare mask (schema-bound rejection):** a mask covering the
ENTIRE frame (masked-out area = 100%) is rejected at VALIDATION time by the schema
itself: `mask_area_budget_pct` carries `maximum: 30` in shot-plan.schema.json, so a
beat declaring a bare-frame mask fails the schema bound before any runtime budget
check runs (state both layers: schema bound `maximum: 30`, then the runtime budget
check — the schema rejects earlier and harder).

**Negative case 4 — EMPTY `critical_regions` + a 29.9% mask:** a beat with an empty
`critical_regions` array and a mask covering 29.9% of the frame passes the area check
numerically, but MUST FAIL: exact/hybrid beats require a NON-EMPTY `critical_regions`
array — with no declared critical regions the subset assertion is vacuously true
(masks are trivially a subset of nothing), which is precisely the mask-away-the-hard-
parts abuse the budget exists to prevent. Empty critical regions on an exact/hybrid
beat = validation FAIL.

**Approval-identity rule (worked FAIL row):** every approval-worthy event records
`approved_by` ∈ {user, leader} AND an `approval_ref` that resolves to a real artifact
(e.g. `.agents/approver/<slug>-tracking.md` iteration). A threshold delta between
golden generations WITHOUT a matching approval record = FAIL:

| generation | reason_class | approved_by | approval_ref | Verdict |
|---|---|---|---|---|
| N | threshold_change | (empty) | (empty) | **FAIL** — approval identity missing; the delta is unrecorded and the cut is blocked |
| N+1 | threshold_change | user | .agents/approver/threshold-change-tracking.md#iter-2 | PASS — approval recorded and resolvable |

### 3.13 Overhead measurement

Per-beat per-tier instrumented fields (gate-sets.json G-GOLDEN overhead): `tier_e_hashing_wall_ms`,
`tier_p_metric_ms`, `tier_r_sampling_ms`, `tier_s_vision_call_ms`, `total_verification_ms`
vs `beat_render_wall_ms`. Budget ≤ 15%; exceedance reported plainly with the DOMINANT
COST named.

**Pre-estimate BEFORE vision calls:** the per-beat verification cost is estimated from
the sampling manifest BEFORE any Tier S vision call is spent; if the estimate breaches
the budget, the vision calls are NOT made — the beat is flagged instead (spend-then-
discover is the failure mode this step exists to prevent).

**Exceedance = FAIL unless a recorded waiver.** An over-budget beat FAILS the overhead
check unless a waiver is recorded with the approval identity (approved_by ∈ {user,
leader}); a silent pass-through is forbidden.

**Fan-out is BOUNDED, not per-sample.** Adaptive sampling yields ~21 samples/beat
(~70 across v5's 2010 frames — HANDOFF §3 + the sampling-manifest shape in §3.9); a
per-SAMPLE vision-call policy would spend ~21 calls/beat ≈ 31,500 ms/beat — roughly
7× the bounded worst case, and up to ~10-15× on longer beats where the 30-frame
baseline yields more sample points. The ≤3-keyframes/beat bound (§3.10) is therefore
visibly load-bearing: the table's Tier S column prices ONE call/beat (1,500 ms — the
typical case); the bounded worst case is 3 calls = 4,500 ms/beat; the unbounded
per-sample case is the breach the bound exists to prevent.

**Cost table over the ACTUAL v1-v5 fixture inventory (file sizes MEASURED tonight via
stat; frame counts from HANDOFF §3; durations from HANDOFF §3; per-tier ms ESTIMATED
at a stated throughput assumption, labeled honestly):**

| Fixture | Size (B, MEASURED) | Frames (HANDOFF §3) | Duration (s) | Beats | Tier E est. (ms) | Tier R est. (ms) | Tier P est. (ms) | Tier S est. (ms) | Total est. (ms) | Budget 15% of render (ms) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| prod (v1) | 3,075,656 | 1341 f | 44.7 | 9 (HANDOFF §3) | 40 | 20 | 900 | 13,500 | 14,460 | 6705 | **EXCEEDS (est.)** — dominant cost: Tier S ≈ 93% of total (13,500/14,460) |
| prod-v2 (v2) | 3,942,562 | 2490 f | 83.0 | 1 (one-shot, HANDOFF §3) | 75 | 37 | 100 | 1500 | 1712 | 12,450 | PASS (est.) |
| prod-v3 (v3) | 2,367,558 | 2010 f | 67.0 | 1 (one-shot, HANDOFF §3) | 60 | 30 | 100 | 1500 | 1690 | 10,050 | PASS (est.) |
| prod-v5 (v5 full) | 2,468,574 | 2010 f | 67.0 | 1 (one-shot, HANDOFF §3) | 60 | 30 | 100 | 1500 | 1690 | 10,050 | PASS (est.) |
| prod-v5-trim12 | 2,161,800 | 1650 f | 55.0 | 1 (one-shot trim, HANDOFF §3) | 50 | 25 | 100 | 1500 | 1675 | 8250 | PASS (est.) |

Estimate basis (every cell follows from it): Tier E ≈ 30 ms per 1000 frames
(masked-region hashing — E = frames ÷ 1000 × 30); Tier R ≈ 15 ms per 1000 frames
(scene sampling — R = frames ÷ 1000 × 15); Tier P ≈ 100 ms per beat (SSIM/dhash on
representative frames — P = beats × 100); Tier S ≈ 1500 ms per beat (vision call,
dominant cost when present — S = beats × 1500). Beat counts: v1 = 9 (HANDOFF §3,
"9 beats"); v2/v3/v5/trim12 = 1 each (HANDOFF §3 documents v2/v3 as "one-shot"; v5
full and trim12 are a single continuous stream-copy pair — per-fixture assumption
"1 beat" labeled here explicitly). Budget column = 15% × duration in ms. Worked
cells (v1): E = 1341/1000 × 30 = 40.2 → 40; R = 1341/1000 × 15 = 20.1 → 20;
P = 9 × 100 = 900; S = 9 × 1500 = 13,500; total = 14,460. **MEASURED numbers land
at activation step 10's film/goldens verification against the v5 known-good cut** —
every per-tier ms above is ESTIMATED at the stated throughput; only file sizes are
MEASURED (stat, read-only, tonight). Do not present estimates as measurements. The v1 row
deliberately demonstrates the exceedance-reporting shape: reported plainly, dominant
cost named (Tier S); the disclosed remedy class is Tier-S sampling reduction via the
per-beat `semantic_checklist` config (approval-worthy, never silent). MEASURED
numbers land at activation step 10 and re-judge this row. All other rows PASS (est.)
as computed.

---

## 4. Vision-Channel Verification (the team can WATCH what it makes)

The hard v1 requirement (A §4): the team must see its output, and the human must be able
to watch it, BEFORE any mission ships. v1-v5 shipped five cuts with **zero eyes on the
full product** (HANDOFF §9 — "every visual claim is a pixel measurement"); this section
is how that gap stays closed forever after activation.

### 4.1 (a) Static preview-page verification — `preview.html` shape (OQ4 default)

**OQ4 DEFAULT TAGGED PENDING-MORNING-APPROVAL: v1 ships preview-page-only; the
direct-MP4 daemon deltas (V1-V5, A §4.3) are DEFERRED.** The preview page needs zero
daemon changes (A §4.2), so its verification is fully static:

| Check | Mechanism | Expected |
|---|---|---|
| MP4 inlined | grep `data:video/mp4;base64` → ≥ 1 hit | present |
| Contact sheet inlined | grep `data:image/png;base64` → ≥ 1 hit | present |
| Zero external URLs | `grep -oE 'https?://[^"'"'"' >)]+' preview.html` → empty | empty (the app's Bootstrap CDN lives in the PRODUCT, not the page — HANDOFF §5) |
| No remote scripts | `grep -c '<script'` → 0 | 0 |
| Single self-contained file | one `.html`, few MB (mp4 + sheet inflate it ~4-6 MB) | e.g. v1 = 4,362,785 B, v2 = 5,850,996 B |

**TONIGHT (pre-run evidence, §6c): both retained lab previews pass all five checks.**
The shape the team must reproduce is proven by the v1-v5 artifacts themselves.

Activation-day (first production mission): the producer's minted `preview.html` for
`out/<iter>/` is re-checked with the same five greps BEFORE `view_link` mints the URL —
a preview that silently references an external URL or drops the contact sheet fails
delivery (B producer workflow step 5; B `video-project-ops.md` Delivery Shape).

### 4.2 (b) Servable-root + grant activation checks (A §4)

These are the read-mostly checks that prove the HUMAN's watch path exists end-to-end.
Items marked **TONIGHT** were verified statically against B's files (§6c); the rest are
activation-day procedures on the dev surface (never the prod daemon tonight).

| # | Check | How | Expected | Status |
|---|---|---|---|---|
| 1 | `planning` root serves `.html` as `text/html` | MIME map entry `daemon/services/live_views.py:183` (A §4.2) — code read, no change needed for v1 | `.html` → `text/html`, `.png` → `image/png` (`:193`) | **TONIGHT: code-read only** (A cites the lines; the map is seeded, no delta required for the preview lane) |
| 2 | `view_link('planning', '<shortname>/video/<project-id>/out/<iter>/preview.html')` mint shape | `daemon/tools/live_views.py:113` (A §4.2) — activation-day: one dev-surface mint + one passcode verify through `/views/public` (OAuth fronting exempts it, `daemon/routers/live_views.py:16-19`) | passcode + public URL returned; page renders inline | activation-day procedure |
| 3 | `view-views` privileged grant present in producer + editor metas | grep `tools.allow` in B's metas (privileged categories are never auto-granted — the empty-allow universe strips them, `daemon/tools/_tool_registry.py:446`, strip at `daemon/tools/instance.py:7608`) | `video-producer` ✓, `video-editor` ✓, `video-capturer` ✗, `video-verifier` ✗ | **TONIGHT: verified** (§6c) — pending OQ1 ruling. If OQ1 = deny, both become ✗ and step 2b is skipped (deny branch) |
| 4 | Commissioned-set pins cover the two new holders | `tests/unit/tools/test_live_views_tool.py:244-350` gains both ids + the registry comment `daemon/tools/_tool_registry.py:457-470` names them (V5, A §4.3) | pin test green at activation | activation-day (only if OQ1 = grant) |
| 5 | V1-V5 delta verify-column checks | ONLY if the user lands direct-MP4 (OQ4 = land-now): per-delta Verify cells of A §4.3 — V1 `mime_for_extension("mp4") == "video/mp4"` + inline play; V2 `Range: bytes=0-1023` → `206` + `Accept-Ranges: bytes`; V3 boot INFO shows 4 roots + `view_link('video-artifact', …)` mints + disallowed ext → uniform 404; V4 **preflight P8 asserts deliverable < 32 MiB** (MAX_SERVED_BYTES — the real serve cap; the threshold is derived FROM the cap, no invented margin; an over-cap file returns a uniform silent 404 at `live_views.py:1279-1283`, so P8 makes the failure loud and early) — P8 is stat-only and runs in <1 s; V5 pin run | per A §4.3 Verify column | **DEFERRED by default** (OQ4 = preview-only) |

Note the asymmetry: the TEAM's eyes (Channel A) need nothing here — the `image` category
reads contact sheets/stills from disk today (A §4.1); all of §4.2 is the HUMAN's channel.

### 4.3 (c) The eyes-on protocol (G-EYES) — closing the zero-eyes gap

Per B's verifier Cardinal #1 ("gates are machines; eyes are mandatory") and skill
`video-gates.md`:

1. Verifier builds the contact sheet + keyframe stills into the project tree's review
   surface (every clip, not a sample).
2. Verifier **READS** them via the `image` category and writes one honest sentence per
   clip naming what it saw (the verdict table carries machine evidence; the eyes notes
   carry judgment).
3. Producer reads the same sheet before delivery (B producer workflow step 5) — two
   independent eye passes per iteration.
4. The human gets the preview URL with the veto window open (A §8.3) — the third,
   decisive eye. HANDOFF §9's standing advice ("give it one human viewing pass before
   wide sharing") becomes a **structural step**, not a plea.

Honesty rule carried from v1-v5 (HANDOFF §6.8): never fake a product state; the
untouched-state stills ship as honesty artifacts. The eyes protocol is what would have
caught the v5 caption-side labelling slip (HANDOFF §9 — the G10 gate said `right`, the
pixels said `LEFT`): a measurement gate judged geometry; only eyes judge *what the film
shows*. Tonight's honest state: zero eyes on v1-v5, documented; first gated mission is
the first eyed cut.

---

## 5. Morning-Review Checklist for the User

Everything below is checkbox-shaped for direct use. Read order, **7 decision blocks**
(OQ1, OQ2, OQ4, OQ-B1, OQ-B2, OQ-B3, OQ-B4 — OQ3 resolved by user ruling
2026-11-12), spot-checks, approval gate, and what approval triggers. Time budget
≈ 30-45 min (A is 900 lines; the rest is spot-checks).

### 5.1 Read

- [ ] Read `.agents/shared/planning/video-team/architecture.md` (A — THE CONTRACT) first.
- [ ] Read B's tree: the four `agents/video-{producer,capturer,editor,verifier}/` dirs
      (`meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`,
      `skills-template/*`) and the seven `data/*.json` contracts.
- [ ] Read this plan (`test-plan.md`).
- [ ] Optional context: the lab's `HANDOFF.md` (209 lines) — the v1-v5 retrospective A §0.2
      traces to.

### 5.2 Decide (one block each — recommendation stated; OQ ledger refs to A §14 + addendum)

- [ ] **OQ1 — `view-views` commissioned set.** Extend to `video-producer` + `video-editor`?
      This is a user-visibility policy change (read-only URL minting; pins + registry
      comment ride the activation PR, A §4.2/V5). **Recommend: GRANT BOTH** — the team
      cannot hand you preview URLs otherwise. (Revoking later = revert two meta entries +
      pins; delivery degrades to artifact paths, B `video-producer/tools_note.md`.)
      **Deny branch (OQ1 = deny):** strip `view-views` from producer+editor metas (no
      privileged grant), skip step 2b entirely, delivery degrades to artifact paths instead
      of preview URLs — the team can still produce and gate, but cannot mint preview URLs.
- [ ] **OQ2 — subject-project registration (verification-class per A §14 —
      RECLASSIFIED iteration 2, NOT user-decision-class; kept listed for traceability,
      no approval gate; do not block activation on it).** Verify/register the subject
      repo (e.g. `/Users/nguyenminhkha/All/Code/Yedda/frontend-engine`) as an ensemble
      project — step 1 of §12 carries the default: verify via API/DB read, register if
      absent (operator action, not code). Without it, the `planning` root has no
      shortname to resolve and Channel B has no workdir.
- [ ] **OQ4 — direct-MP4 daemon deltas (V1-V5, A §4.3).** Land now, or preview-page-only v1?
      **Recommend: PREVIEW-PAGE-ONLY FOR v1, DEFER V1-V5** — zero daemon change; you can
      watch tonight-shaped output (§4.1 evidence); land V1-V5 before any sharing beyond
      local human review.
- [ ] **OQ-B1 — prompt-artifact baseline (corrected recipe, A §14).** Accept that
      activation re-runs the baseline generator — its OUTPUT COUNT is the pin, not a hand
      constant: 181 manifest rows + 16 generator rows = **197** — and re-pins
      `BASELINE_EXPECTED_ENTRIES` 177 → **197** in the same activation commit. The 177
      pin is ALREADY STALE at base (manifest holds 181); the coverage test
      (`test_the_baseline_covers_every_duty_file`) REDs on-branch BY DESIGN until the
      generator re-run (documented, not hidden — §6c). **Recommend: ACCEPT.**
- [ ] **OQ-B2 — skill-roster re-derivation (corrected recipe, A §14).** Accept that
      activation re-derives the roster manifest, then bumps ALL of: census rows
      46 → 50, declaring 25 → 29 (= 15 live + 14 not-live, not-live(declaring)
      10 → 14), declaring-nothing 21 (unchanged), **not-live over ALL rows
      31 → 35 (`test_skill_roster_manifest.py:102` — the missing row, taken from the
      pin file)** — now also enumerated in A §14's corrected OQ-B2 row, verified at
      finalize time, registered agents 45 → 49,
      registered-not-live 30 → 34, and extends `false_agents` with the four sorted
      names (`video-capturer`, `video-editor`, `video-producer`, `video-verifier`). Until
      then the two manifest-diff pins RED on-branch BY DESIGN (§6c) — the count pin
      (`test_census_counts_are_the_published_ones`) stays GREEN because it reads the
      un-re-derived manifest. **Recommend: ACCEPT.**
- [ ] **OQ-B3 — canary duty-block (new, A §14).** Accept the documented
      PRE-EXISTING-AT-BASE red: `agents/canary/rule.md` carries no duty block, so
      `test_every_duty_file_carries_the_byte_identical_block` REDs at base before any
      scaffold change. Activation-day: append the byte-identical duty block to
      `agents/canary/rule.md` (the same template append), in the SAME commit as the
      OQ-B1 baseline re-pin. **Recommend: ACCEPT.**
- [ ] **OQ-B4 — snapshot pin (STOPPED, A §14).** `tests/unit/tools/test_snapshot_tools.py:1835`
      (`test_gate_flag_enabled_on_targets`) is RED at base INDEPENDENTLY of the scaffold:
      base derivation (excluding `agents/video-*/`) yields `['developer', 'developer',
      'tester']` because BOTH `agents/developer/meta.json` and `agents/developer[v2]/meta.json`
      carry `snapshot_enabled: true` with the shared agent_id `developer`; the pin expects
      `['developer', 'tester']`. The pin is NOT extended and the four video metas keep
      `snapshot_enabled: true` (design decision stands). Remediation is pre-existing-debt
      work (remove `snapshot_enabled` from v1 `developer/meta.json`, or dedupe by agent_id
      in the test) — OUTSIDE this scaffold's scope, reported, not applied. No user action
      required; documented so the morning runner is not surprised by a red that looks
      scaffold-caused. **Recommend: NOTE, do not block activation on it.**

### 5.3 Spot-check (read-only commands, expected outputs embedded — full results in §6)

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-video-team-scaffold
git status --short
# expected SHAPE (re-derive with git status — the tree is concurrent; do not trust a
# memorized list): the scaffold set (sanctioned deltas + the video trees) plus any
# concurrent dev-1/approver edits; what matters is that the SANCTIONED files are the
# only scaffold-side modifications and the two approver artifacts are not yours.
# Snapshot-pin pack: STOPPED per OQ-B4 (A §14) — the pin is NOT extended; the four
# video metas keep snapshot_enabled: true; the red at test_snapshot_tools.py:1835 is
# pre-existing-debt, not scaffold-caused.
git diff --stat
# expected SHAPE: the sanctioned delta files (3 pin packs + .gitignore) dominate;
# count and ±lines drift as dev-1 lands — re-derive, don't trust a number.
git check-ignore -v .agents/shared/planning/video-team/data/gate-sets.json
# expect: NO OUTPUT, exit 1 — the 7 contracts are NOT ignored (the one negation works;
#         check-ignore exit 1 = not-ignored, so `echo $?` right after must print 1)
git check-ignore -v agents/video-producer/data/whatever.txt
# expect: `.gitignore:28:data/\tagents/video-producer/data/whatever.txt`, exit 0 —
#         an unrelated data/ path still matches the GENERIC rule (correct scoping:
#         only the planning/video-team/data/ dir is negated)
python3 -c "import json,glob; [json.load(open(p)) for p in glob.glob('.agents/shared/planning/video-team/data/*.json')+glob.glob('agents/video-*/meta.json')]; print('json ok: 11')"
# expect: `json ok: 11`
grep -c '"view-views"' agents/video-producer/meta.json agents/video-editor/meta.json agents/video-capturer/meta.json agents/video-verifier/meta.json
# expect: 1,1,0,0  (OQ1's grant lives exactly in producer+editor)
ls tests/unit/test_video_*_agent.py
# expect: No such file (activation §12 step 4 creates them — documented absence)
grep -rn "agents/video" agents/.prompt-artifacts.sha256 | wc -l
# expect: 0 (the +16 baseline rows do not exist yet — OQ-B1's RED is real)
python3 - <<'PY'
import hashlib, pathlib
for a in ("video-producer","video-capturer","video-editor","video-verifier","worker"):
    t = pathlib.Path(f"agents/{a}/rule.md").read_text()
    print(a, hashlib.sha256(t[t.index("## Reporting Anomalies"):].strip().encode()).hexdigest()[:16])
# expect: five IDENTICAL hash16 values (5e376731cf9d977f) — the duty block is byte-identical
PY
grep -c '^```' agents/video-producer/skills-template/video-project-ops.md
# expect: an EVEN number (fence parity — the odd-fence guard's shape)

# --- CONTRACT ASSERTIONS (demonstrate before activation — tonight-runnable, daemon-free) ---
python3 -c "import json;d=json.load(open('.agents/shared/planning/video-team/data/gate-sets.json'));print(d['gates']['G-GOLDEN']['tiers']['perceptual']['calibration']['uncalibrated_fallback'])"
# expect: {'ssim_min': 0.995, 'dhash_hamming_max': 2}
python3 -c 'import json;d=json.load(open(".agents/shared/planning/video-team/data/shot-plan.schema.json"));print(d["$defs"]["beat"]["properties"]["comparison_thresholds"]["properties"]["representative"]["properties"]["mode"]["enum"])'
# expect: ['scene', 'every_n', 'adaptive'] — enum CONTAINS 'adaptive'
python3 -c 'import json;d=json.load(open(".agents/shared/planning/video-team/data/shot-plan.schema.json"));print(d["$defs"]["beat"]["properties"]["mask_area_budget_pct"]["maximum"])'
# expect: 30 (schema-bound mask rejection)
python3 -c "import json;d=json.load(open('.agents/shared/planning/video-team/data/lane-profiles.json'));print('_note' in d['profiles']['film+goldens']['comparison_thresholds_default'])"
# expect: True — the film lane carries the calibration-seed note (PROPOSED INITIAL VALUES / calibration seeds, superseded by derived thresholds at mint; uncalibrated fallback 0.995/2)
```

### 5.4 Approve the activation plan

- [ ] Approve A §12's ordered activation plan (steps 1-10, with 2a/2b sub-steps).
      **Approving means steps 2-10 execute IN ORDER under morning supervision** (step 2a
      only if OQ4 = land-now; step 2b only if OQ1 = grant; step 7a authoring the
      preflight executor runs BEFORE step 7's self-test consumes it).
      Rollbacks are per-step in A §12; nothing executes without this box.

### 5.5 What "approved" triggers next

1. Step 3 — **tool-resolution proof** (spawn-blocking): the four agents dry-resolve on a
   DEV instance; every `tools.allow` entry must resolve to an existing category **OR a
   known literal tool name** — `subtree_messages`/`subtree_status` resolve via the
   literal-name leg (`daemon/tools/instance.py:575`, the `else: allowed_tools.add(item)`
   branch), so "category-only" is the wrong assertion shape and would false-fail them
   (zero fail-closed surprises at first dispatch).
2. Step 4 — **census pins**: the four `tests/unit/test_video_*_agent.py` packs are created
   and green (exact-equality, mirroring `tests/unit/test_maintenancer_agent.py`).
3. Step 5 — **leader wiring**: `agents/leader/meta.json` `team_members` += the four ids;
   every roster pin updated in the same PR.
4. Step 6 — **lane minting + registry**: leader mints producer once; producer mints its
   three; instance ids land in `shared_meta_kv` (`video.lanes.<project>.<role>`). `snapshot_enabled`
   is armed ON-BRANCH for all four (A §2.2/§12 step 6); first snapshots are taken AT
   mint time for the three executors (warm-start inheritance per A §2.2).
5. Step 7 — **standing-lab arm**: capturer arms the lab under `service` management and
   runs **P1-P8 self-tests against the v5 known-good cut** (P8 now exists in `data/preflights.json` — landed this round) (this plan §1 + §2 expected
   outcomes) — the best possible base proof before any new capture. (Step 7a authors
   `video-lab/tools/preflight.mjs` BEFORE this step consumes it.)
6. Step 8 — **first mission** (production lane, `fast-demo`): one beat re-capture + one
   trim + preview mint end-to-end; G-set green + a human opens the preview URL — **the
   first eyed cut in the project's history** (§4.3).
7. Steps 9-10 follow when ready: hot-lane acceptance (< 1 min text swap, veto window
   present), then film profile + goldens + the HyperFrames evaluation mission (A §3.2).

---

## 6. Tonight's Static Machine-Checks — RUN, with results

Zero-dependency checks executed tonight (system `python3`, `grep`, `git diff`; no venv,
no pytest, no daemon contact, no lab execution — lab access was read-only `ls`/file reads).
Everything below labeled **TONIGHT** is real executed output; anything not runnable
tonight is labeled **DOCUMENTED** and lives in §1/§2/§4/§5.

### 6a. Parse checks — TONIGHT: all green

```
JSON-PARSE: 11/11 parsed OK   (7 data/*.json + 4 agents/video-*/meta.json; python3 json.load)
YAML-PARSE: 4/4 skill-set.yaml parsed OK, agent_id matches in each
            (system python3 HAS pyyaml — no morning deferral needed)
FENCE-PARITY: 22/22 prompt files carry an even ``` fence count (odd-fence guard shape)
```

### 6b. Prompt-section-reference integrity greps — TONIGHT: clean (one exempt hit)

Approximation of `tests/unit/tools/test_prompt_section_reference_integrity.py` detectors
over the 22 in-scope files (4 agents × soul/rule/workflow/tools_note + 6 skills-template).
**The test file is the authority; these greps are its logic mirrored, not a replacement.**

| Detector (mirrored regex) | Hits | Verdict |
|---|---|---|
| empty-capture `(See )` | 0 | clean |
| glued words (`whichload_skill` / `eitherAllowed` / `andcore.md` / `matchedload_skill`) | 0 | clean |
| broken-prose See-splice (`\w+ See <Capital>`) | 0 | clean |
| bare `.md` filename token | **1 raw hit** | **exempt by the authority's own rules — see below** |
| cross-agent path token (`<agent>/rule.md` shape) | 0 | clean |
| bare `agents/` prefix before a capital | 0 | clean |

The single raw hit: `brief.md` inside the PROJECT-TREE fence of
`agents/video-producer/skills-template/video-project-ops.md` (the canonical layout
diagram, B's `video-project-ops.md` §Project Tree). The authority test **skips tokens
inside fenced code blocks** (`FENCED_CODE_BLOCKS_RE`, and the §12.5 #0
operational-filesystem exclusion) — `brief.md` is an operational filename in a fenced
diagram, not a cross-reference. **Expected real-test verdict: GREEN.** Morning runs the
real test (`pytest tests/unit/tools/test_prompt_section_reference_integrity.py`) as the
binding check; if it disagrees with this approximation, the test wins and B gets a
`skill_fix`-class repair.

**Scoped verdict (executed ground truth):** 22 video-referencing files → GREEN;
suite-level = **5F / 1762P**, all 5 pre-existing at base (zero video-* in the failure
set). The 5 failures are base-leg defects, not scaffold-caused: developer ×2 files
(both carry `snapshot_enabled: true` with shared agent_id `developer`); charter
install-mermaid-cli.md odd fence (~:75); charter rule.md odd fence; charter workflow.md
odd fence. None is in the video scope; the morning runner must not read any of the 5
as a scaffold regression.

Same-agent `See <Section Name>` references found (the sanctioned form), with resolution
targets grep-verified tonight:

| Ref site | Section referenced | Resolves to |
|---|---|---|
| `agents/video-producer/workflow.md:35` | Action-Log Governance (the human veto) | `skills-template/video-project-ops.md:34` |
| `agents/video-producer/workflow.md:43` | Delta Intake | `rule.md:34` |
| `agents/video-producer/rule.md:19` | Cap And Flag | `rule.md:68` |
| `agents/video-producer/soul.md:41` | Delta Intake (video-producer rule) | `rule.md:34` |
| `agents/video-producer/soul.md:51` | Tool-by-tool boundary | `tools_note.md:15` |
| `agents/video-producer/soul.md:64` | Cap And Flag | `rule.md:68` |

**True record:** the two refs at `workflow.md:35` and `soul.md:51` WERE dangling at first
authoring — caught in review, then re-pointed; both now resolve (line numbers above,
verified tonight). The integrity gate itself validates FORMAT (section-name shape, no
path tokens); semantic RESOLUTION of cross-surface section names is flagged for
activation day per A §12 step 3 (the composed-prompt read at spawn), not covered by the
offline gate.

**File-level resolution caveat (N6):** `video-project-ops` is `auto_load: false`
(`skill-set.yaml` verified) — so a `See Action-Log Governance (the human veto)` reference
resolves only when the skill is SURFACED (producer's skill surfacing), not at prompt
composition time. Resolution of such refs is therefore FILE-LEVEL and AVAILABILITY-GATED
on skill surfacing; the activation-day check must read the composed prompt WITH the skill
loaded, not the base prompt alone. This is the reason the resolution check is flagged for
activation day rather than verified offline.

### 6c. Census-pin coherence — TONIGHT: diffs read, REDs expected and DOCUMENTED

**The sanctioned deltas on THIS tree: ONE `.gitignore` negation + the three pin packs**
(A §13; `git diff` read tonight; extended by nobody). A snapshot-pin pack was considered and STOPPED per OQ-B4 (A §14) — the pin is NOT
extended and the four video metas keep snapshot_enabled: true; the red at
test_snapshot_tools.py:1835 is pre-existing-debt, not scaffold-caused. No fifth
modified file is expected; the sanctioned-delta list stays at four (re-check on
the day; never invent a file that isn't there):

| File | What changed | Was → Now |
|---|---|---|
| `.gitignore` | +4 lines: 3 comment lines + the ONE leader-authorized negation `!.agents/shared/planning/video-team/data/` (so the generic `data/` rule does not swallow the 7 contracts of record) | 0 → +4 lines (`git diff` = ` M .gitignore`) |
| `tests/unit/test_anomaly_report_duty_prompts.py` | `EXPECTED_TOTAL`, `EXPECTED_WALK` (+ comment), at `:99-101` | 45 → **50**, 43 → **48** (specials 2 unchanged) — the four video `rule.md` files carry the byte-identical duty block |
| `tests/unit/test_anomaly_report_grant_pins.py` | `EXPECTED_EXPLICIT`, `EXPECTED_TOTAL_META` | 44 → **48**, 46 → **50** — the four agents carry explicit `nest-report` grants |
| `tests/unit/test_pok_grant_pins.py` | `EXPECTED_EXPLICIT`, `EXPECTED_TOTAL_META` | 45 → **49**, 46 → **50** — the four agents carry explicit `pok` grants |

`git diff --stat` = **4 files, +39/−23** (3 pin files + .gitignore).

Cross-checks that EXPLAIN the bumps (TONIGHT):

```
DUTY-BLOCK sha16: all four video rule.md blocks hash 5e376731cf9d977f —
  byte-identical to each other AND to worker/rule.md's (the template carrier)
GRANTS: pok=True, nest-report=True in ALL FOUR metas (the +4s in both grant pins)
  view-views: producer ✓ editor ✓ capturer ✗ verifier ✗  (OQ1 grant scope, exactly A §4.2)
  snapshot_enabled=true ×4; team_members: producer→[capturer,editor,verifier], leaves→[]
  skill_injection=false ×4, innate=[dynamic-skill, todo], version 1.0.0 ×4
META CENSUS: 50 dirs carry agents/*/meta.json (was 46 → +4, matching both TOTAL_META bumps)
```

**On-branch RED expectations — POST-FIX arithmetic, DOCUMENTED, NOT HIDDEN
(OQ-B1 / OQ-B2 / OQ-B3).** With the 50/48 duty-pin fix now ON-BRANCH
(`EXPECTED_TOTAL = 50`, `EXPECTED_WALK = 48`, `EXPECTED_SPECIALS = 2`, at
`tests/unit/test_anomaly_report_duty_prompts.py:99-101`), what still REDs on this
branch attributes as follows — pre-existing-at-base vs branch-by-design, stated
per red:

| Red | Attribution | Tonight's measured state | Resolution |
|---|---|---|---|
| (a) Baseline-coverage red — `test_the_baseline_covers_every_duty_file` (`test_anomaly_report_duty_prompts.py:586` — advisory) | **BRANCH-BY-DESIGN** (the four new `agents/video-*/rule.md` duty files did not exist at base). Its sibling COUNT red — `test_the_baseline_matches_the_real_bytes` (`~:560` — advisory, assert `~:568`, pin `BASELINE_EXPECTED_ENTRIES = 177` at `:207`) — is **PRE-EXISTING AT BASE** (manifest 181 ≠ pin 177) | manifest: **181 non-blank rows, ZERO `video-` rows** (grep count 0); pin 177 unchanged — B could not touch the manifest | activation: re-run the baseline generator (its output count is the pin: 181 manifest rows + 16 generator rows → **197**), re-pin to the GENERATOR OUTPUT — **197**, not a hand constant — same commit (OQ-B1). The base staleness (177-vs-181) is why the order is generator-re-run THEN re-pin |
| (b) Canary duty-block red — FOUR canary tests trip (test IDs canonical; line refs advisory): `test_every_duty_file_carries_the_byte_identical_block` (~:354, endswith walk) — trips; `test_the_duty_block_is_the_last_top_level_section` (~:366, SLICING site, NOT endswith) — trips; `test_no_duty_file_carries_a_forbidden_token_inside_its_duty_block` (~:446) — trips; `test_each_duty_file_keeps_its_cardinal_count` (~:475) — trips. `test_each_pair_carries_the_duty_block` (~:416) does NOT trip (iterates V2_PAIRS; canary is not a pair member). Baseline reds are SEPARATE rows below — do NOT fold them into this row | **PRE-EXISTING AT BASE** (OQ-B3 — `agents/canary/rule.md` carries NO duty block; `grep -c "Reporting Anomalies"` = 0 tonight; canary is an existing agent outside the scaffold's write scope) | zero "Reporting Anomalies" blocks in `agents/canary/rule.md` | activation-day: append the byte-identical duty block to `agents/canary/rule.md` (the SAME template append), in the same commit as the OQ-B1 baseline re-pin (OQ-B3) |
| (c1) Roster drift — `test_manifest_matches_the_rederived_census` (`tests/services/test_skill_roster_manifest.py:~:72` — advisory) | **BRANCH-BY-DESIGN** — the derivation reads live `agents/*/meta.json`, which now holds the four video agents | derived census 50 entries vs published manifest 46 → drift names exactly `video-capturer`, `video-editor`, `video-producer`, `video-verifier` | activation: re-derive `roster.md` + bump the pins (OQ-B2) |
| (c2) Roster drift — `test_manifest_lists_every_census_entry` (`test_skill_roster_manifest.py:~:84` — advisory) | **BRANCH-BY-DESIGN** — same cause (set difference manifest vs derived) | same four names | same (OQ-B2) |

**Correction on a previously-predicted red:** `test_census_counts_are_the_published_ones`
(`test_skill_roster_manifest.py:96`) is **GREEN on-branch**, not red — it asserts over
the PARSED MANIFEST (still at rows 46 / declaring 25 / not-live 10 / declaring-nothing
21), and the manifest file has not been re-derived. The drift surfaces through the two
diff pins above, not through the count pins. The count-pin bumps land WITH the re-derivation at activation (OQ-B2): rows
46 → 50; declaring 25 → 29 = 15 live + 14 not-live; not-live(declaring) 10 → 14;
declaring-nothing 21 (unchanged); **not-live over all rows 31 → 35 (`:102` — now also enumerated in A §14's
corrected OQ-B2 row, verified at finalize time)**; registered 45 → 49;
registered-not-live 30 → 34; `false_agents` += the four sorted names.

| (d) Prompt-integrity suite — `tests/unit/tools/test_prompt_section_reference_integrity.py` | **PRE-EXISTING AT BASE / base-leg** (executed ground truth: **5F / 1762P, ZERO video-* in the failure set**) | all 5 failures are base-leg defects, not scaffold-caused: developer ×2 files (both carry `snapshot_enabled: true` with shared agent_id `developer`); charter install-mermaid-cli.md odd fence (~:75); charter rule.md odd fence; charter workflow.md odd fence. None is in the video scope | the morning runner must not read any of the 5 as a scaffold regression; record the file-level names the executed pack reports, do not invent mechanisms it doesn't state |
**Documented absences (expected, by design):**

```
tests/unit/test_video_producer_agent.py   — ABSENT (activation §12 step 4 creates)
tests/unit/test_video_capturer_agent.py   — ABSENT (  "  )
tests/unit/test_video_editor_agent.py     — ABSENT (  "  )
tests/unit/test_video_verifier_agent.py   — ABSENT (  "  )
```

### 6d. Preview-page static shape — TONIGHT: verified on the real artifacts

```
prod/preview.html      (4,362,785 B): 1× data:video/mp4;base64, 1× data:image/png;base64,
                       0 external http(s) refs, 0 <script> tags
prod-v2/preview.html   (5,850,996 B): same shape — all five §4.1 checks pass
```

### 6e. Tonight's exact commands (reproducibility record)

The §6a-§6d results were produced by, and are re-runnable with:

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-video-team-scaffold
python3 - <<'PY'
import json, glob
[json.load(open(p)) for p in glob.glob('.agents/shared/planning/video-team/data/*.json')
                       + glob.glob('agents/video-*/meta.json')]; print('json ok: 11')
PY
python3 -c "import yaml,glob; [yaml.safe_load(open(p)) for p in glob.glob('agents/video-*/skill-set.yaml')]; print('yaml ok: 4')"
grep -c '"view-views"' agents/video-producer/meta.json agents/video-editor/meta.json \
                       agents/video-capturer/meta.json agents/video-verifier/meta.json   # expect 1,1,0,0
grep -o '"pok"\|"nest-report"' agents/video-*/meta.json | sort | uniq -c   # expect 2 each, total 8 (both grants, every agent — A §13)
# per-file loop (the morning-runner shape): for f in agents/video-*/meta.json; do printf '%s ' "$f"; grep -o '"pok"\|"nest-report"' "$f" | wc -l; done   # expect 2,2,2,2
wc -l agents/.prompt-artifacts.sha256                                                  # expect 181 (PRE-ACTIVATION — becomes 197 after the OQ-B1 re-pin at activation)
grep -c "video-" agents/.prompt-artifacts.sha256                                       # expect 0
ls tests/unit/test_video_*_agent.py                                                    # expect: No such file
ls -d agents/*/meta.json | wc -l                                                       # expect 50
grep -c "Reporting Anomalies" agents/canary/rule.md                                    # expect 0 (OQ-B3 pre-existing red)
grep -n "^EXPECTED_TOTAL\|^EXPECTED_WALK\|^EXPECTED_SPECIALS\|^BASELINE_EXPECTED_ENTRIES" \
     tests/unit/test_anomaly_report_duty_prompts.py                                    # expect 50, 48, 2, 177
git diff --stat   # expect: 4 files, +39/−23 (.gitignore + 3 pin packs)
git check-ignore -v .agents/shared/planning/video-team/data/gate-sets.json; echo "exit=$?"
# expect: no output, exit=1 (NOT ignored — the negation works; exit 1 IS the pass signal)
git check-ignore -v agents/video-producer/data/whatever.txt; echo "exit=$?"
# expect: `.gitignore:28:data/\tagents/video-producer/data/whatever.txt`, exit=0 (generic rule still bites elsewhere)
# See-ref resolution (the two re-pointed refs):
grep -n "Action-Log Governance" agents/video-producer/skills-template/video-project-ops.md   # expect :34
grep -n "Tool-by-tool boundary" agents/video-producer/tools_note.md                          # expect :15
# lab side (read-only):
L=/Users/nguyenminhkha/All/Code/Yedda/video-lab
grep -c "data:video/mp4;base64" "$L/prod/preview.html" "$L/prod-v2/preview.html"       # expect 1,1
grep -oE 'https?://[^"'"'"' >)]+' "$L/prod/preview.html" "$L/prod-v2/preview.html"     # expect: no output
grep -c "<script" "$L/prod/preview.html" "$L/prod-v2/preview.html"                     # expect 0,0
```

The detector regexes of §6b are quoted verbatim from
`tests/unit/tools/test_prompt_section_reference_integrity.py` (lines ~160-260) — that
file is the authority; tonight's greps mirror four of its detectors plus two closure
patterns over the 22 in-scope B files.

### 6f. NOT run tonight (by constraint, all DOCUMENTED for morning/activation)

- **pytest** anywhere (the pins' RED expectations above are derived by reading, not by
  running — running them tonight would also have required a venv, which is banned).
- **venv creation** — banned; morning's pytest commands live in §5.3's activation flow.
- **Anything against any daemon** — no restarts, no API calls, no spawn, no service.
- **Lab execution** — no `node`, no `ffmpeg`, no `ffprobe`, no render, no stack start;
  the §2 dry-runs and §1 self-tests are specs, not runs. Lab access tonight = `ls` +
  file reads (HANDOFF.md, preview.html greps) only.
- **git add / commit / push / merge** — this file is left untracked, like A and B.

---

## Appendix: traceability — every claim in this plan to its source

| Claim | Source |
|---|---|
| P1-P8 definitions, cost caps, exit semantics | `data/preflights.json` (P8 landed — adjudicated spec: deliverable size, 32 MiB, stat-only); A §4.3 V4 row (~:332) + A §5.1 P8 row (~:360, advisory) + A §12 step 7 (~:664-666, advisory) |
| Gate membership + tolerances (2 px / 0.005 px) | `data/gate-sets.json`; A §5.2, §6.2 |
| Iteration caps 3 / 2; goldens off/on; caps 90 s | `data/lane-profiles.json`; A §6.1-§6.4 |
| beat_valid := five pinned shas | `data/invalidation-spec.json`; A §9 |
| The eight v1-v5 catches (R1-R8) | A §0.2; HANDOFF §3/§5/§9 |
| 83 s v2 over-TARGET (under the 90 s cap) cut (P1's in-HANDOFF anchor); 55.0 s/1650 f trim12; 1245/1650 range-render; 0.0044 px pointer; three identical launches | HANDOFF §3, §5, §9 (verified) |
| 60,300-frame runaway | KICKOFF-BRIEF-SOURCED/UNVERIFIED against this lab (no retained artifact; HANDOFF's duration rationale anchors on the 83 s cut) |
| Preview shape (base64 mp4 + sheet, zero external URLs) | HANDOFF §3 + tonight's greps (§6d) |
| Zero-eyes gap | HANDOFF §9; A §4 preamble |
| view-views grant scope + privileged-strip mechanics | A §4.2; `daemon/tools/_tool_registry.py:446`; `daemon/tools/instance.py:7608` |
| V1-V5 deltas + P8 deliverable-size assertion (32 MiB, MAX_SERVED_BYTES — derived threshold, not a hand number) vs 32 MiB serve cap | A §4.3 V4 row (~:332) + `data/preflights.json` (P8) + A §5.1 P8 row (~:360, advisory) + A §12 step 7 (~:664-666, advisory) |
| Eyes protocol (contact sheet + named verdicts) | B `agents/video-verifier/skills-template/video-gates.md`; B verifier `rule.md` Cardinal #1 |
| Duty-block byte-identity + pin bumps | tonight's §6c (sha16 `5e376731cf9d977f` × 5 files) |
| Three-tier golden model (E/P/R) + per-beat comparison config at mint + adaptive sampling + mask validation + overhead budget | user ruling 2026-11-12 (amendment 2); §3.9-§3.13; A §6.3 |

| OQ1, OQ2, OQ4, OQ-B1, OQ-B2, OQ-B3, OQ-B4 (OQ3 resolved by user ruling 2026-11-12) | A §14 + addendum (now 900 lines); §5.2 above |
| Three-tier golden model (E exact / P perceptual / R representative) + per-beat comparison config at mint + Option A storage (lab binaries + in-repo manifests) | user ruling 2026-11-12; §3, §2.2 G-GOLDEN row, A §6.3 |
| Snapshot pin STOPPED (OQ-B4) — test_snapshot_tools.py:1835 RED at base, pre-existing-debt, pin NOT extended, snapshot_enabled stays true ×4 | A §14 OQ-B4 |
| HyperFrames license/cost/capture facts (only E3 semantics referenced here) | A §3.2 — BRIEF-SOURCED/UNVERIFIED: E1 hard-requires reading the upstream license/fee schedule before any film-lane commitment; nothing in C leans on them unverified |
| Sanctioned deltas (.gitignore negation + 3 pin packs + pok/nest-report grants) | A §13; tonight's §6c `git diff` |
