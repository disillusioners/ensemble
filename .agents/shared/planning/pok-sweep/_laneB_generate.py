#!/usr/bin/env python3
"""Lane B (POK sweep wave 2) - cluster derivation + phase-1 completion scan.

Deterministic generator: reads the authoritative listing JSON, pulls every
member body/slug/id MACHINE-SIDE (never retyped), verifies merged-body char
counts and required-phrase coverage, emits the two report md files.
NO database access. NO POK writes (PM owns every POK write).
"""
import json, hashlib, re, os, collections

D = os.path.dirname(os.path.abspath(__file__))
LISTING = os.path.join(D, "live-active-2026-11-18-post-phase1.json")
OUT_CLUSTER = os.path.join(D, "cluster-proposals-2026-11-18.md")
OUT_SCAN = os.path.join(D, "phase1-completion-scan-2026-11-18.md")

doc = json.load(open(LISTING))
ROWS = doc["rows"]
BY = {r["id"]: r for r in ROWS}
BY_SLUG = {r["slug"]: r for r in ROWS}
CAP = 240

agg = hashlib.sha256("\n".join(r["body"] for r in sorted(ROWS, key=lambda x: x["id"])).encode()).hexdigest()
per_row_bad = [r["id"] for r in ROWS if hashlib.sha256(r["body"].encode()).hexdigest() != r["content_sha256"]]
LISTING_SHA = hashlib.sha256(open(LISTING, "rb").read()).hexdigest()
assert len(ROWS) == doc["active_count"] == 277, "row count drift"
assert agg == doc["bodies_digest_sha256"], "bodies aggregate drift"
assert not per_row_bad, "per-row sha mismatch: %s" % per_row_bad

DRIFT = ("Store drift after capture: active 278 (kb-writer insert "
         "gc-fe-surfaces-span-pin-committed-only at 15:32:56Z) and superseded 62->63. "
         "This analysis is bound to the LISTING snapshot (exact-at-capture), not to live store state.")

CLUSTERS = [
 dict(cid="CL-01", rank=1,
   title="P11 git-span / source-gc drift-guard landing shape (near-identical pair)",
   members=["7d38955d-869b-4453-a5af-157e5ff43ec6", "26cbdc9d-e2f4-43d9-939d-05e874263a94"],
   survivor="land-01-merge-shape-guards",
   required=["--no-ff", "first-parent chain", "squash/rebase", "span non-empty",
             "stay RED by design", "empty span diff"],
   variants=[
     "P11 drift guards (TestP11BootNoBehaviorChange) turn GREEN only on a merge --no-ff landing on the first-parent chain; squash/rebase leaves the span non-empty, guards stay RED by design. Verify first-parent merge + empty span diff.",
   ],
   routed_out=[],
   rationale=("Both members assert the same rule in different words: the drift guard only greens on a "
              "--no-ff first-parent merge; a squash/rebase landing leaves a non-empty span and the guard red. "
              "land-01 adds the verification step (first-parent merge + empty span diff); source-gc names the "
              "guard family (P11 source-gc). The union fits the 240-char budget with every durable fact intact."),
   surprise=None),

 dict(cid="CL-02", rank=2,
   title="9797 listener close order + the teardown never holds 9797",
   members=["41408276-aea2-437b-b2e4-507ea11802ad", "59ea590b-89ee-42c5-b453-43dcd452b033",
            "72ef05e8-738d-4d79-afee-b72b034f7b3b"],
   survivor="shutdown-01-teardown-order",
   required=["closes the 9797", "FIRST", "60s drain", "lifespan teardown", "manager.shutdown()",
             "never holds 9797", "9798 closes late"],
   variants=[
     "Daemon wind-down: uvicorn closes the 9797 LISTENING socket FIRST in Server.shutdown(), before the 60s drain, then the api.py lifespan teardown + manager.shutdown(). Teardown never holds 9797; 9798 closes late (api.py step 19).",
     "Daemon wind-down: uvicorn closes the 9797 LISTENING socket FIRST in Server.shutdown() (server.py), before the 60s drain, then the api.py lifespan teardown + manager.shutdown() (manager.py). Teardown never holds 9797; 9798 closes late (api.py step 19).",
   ],
   routed_out=[
     "Line anchors server.py:275-276 / api.py 3987-4506 / manager.py:16514 - file-level anchors retained where they fit; exact line numbers drift (ANCHORS-ARE-LEADS convention).",
     "The 2026-11-11 EADDRINUSE-loop attribution (an orphaned daemon app child held the LISTEN socket; systemd cgroup cleanup freed it at 11:54:52Z) - dated incident, lives in the daemon log / incident record.",
   ],
   rationale=("Three rows assert one mechanism from three angles: the close order (shutdown-01), the "
              "never-holds-9797 consequence + late 9798 close (bind-03), and the EADDRINUSE misattribution "
              "that follows (shutdown-02). Merging the order + the consequence into one row removes the "
              "contradiction surface between the teardown holding 9797 and it not holding 9797."),
   surprise=None),

 dict(cid="CL-03", rank=3,
   title="OpenRouter: existing credential vs missing decision model row",
   members=["fc96dc4b-b385-4bf3-86c9-4f31d34e9e1d", "da205d55-4c98-4bed-97b9-dae06f5e1731"],
   survivor="proxydec-01-openrouter-key-held",
   required=["No new OpenRouter key", "credentials.id=openrouter", "MISSING MODEL ROW",
             "decision-kind row", "typesafe/jev-1.13"],
   variants=[
     "No new OpenRouter key needed - the proxy store holds a working one (credentials.id=openrouter). The gap is a MISSING MODEL ROW, not a credential: add a decision-kind row bound to that credential, internal_model=typesafe/jev-1.13.",
   ],
   routed_out=[
     "Key fingerprint (len 73, sha16 5add86cdf9f95859) - rotating secret inventory, not a durable fact.",
     "serves System One (-> 200) and model ids must be provider-prefixed - both already carried by the ACTIVE row sysone-02-openrouter-route (ce9f71f3), so no fact leaves the store.",
   ],
   rationale=("The two members are the same conclusion from two directions: the credential exists, the "
              "decision lane is unconfigured. Merging makes the actionable half (add the model row) the "
              "headline and removes a duplicate no-new-key-needed statement."),
   surprise=None),

 dict(cid="CL-04", rank=4,
   title="ENSEMBLE_AUTH_* ambient scrub family - the two-member core",
   members=["c15be838-547b-48b0-9544-c190b2fb7ac5", "d51b6509-1dfa-4f97-b4e3-60ade9192d38"],
   survivor="gate-auth-env-302-poison",
   required=["FULL ENSEMBLE_AUTH_* family", "per-variable", "never just ISSUER/DISCOVERY_URL",
             "pydantic-settings", "302-fail", "request_denied_302", "kill_switch config nodes"],
   variants=[
     "Scrub the FULL ENSEMBLE_AUTH_* family per-variable at every gate pytest leg - never just ISSUER/DISCOVERY_URL. Ambient ENSEMBLE_AUTH_ENABLED=true leaks via pydantic-settings: API tests 302-fail (request_denied_302), 2 kill_switch config nodes red.",
     "Scrub the FULL ENSEMBLE_AUTH_* family per-variable at every gate leg - never just ISSUER/DISCOVERY_URL. Ambient ENSEMBLE_AUTH_ENABLED=true leaks via pydantic-settings: API tests 302-fail (request_denied_302), 2 kill_switch config nodes red.",
   ],
   routed_out=[],
   rationale=("These two members carry the same rule with the same two symptom surfaces (the 302 on API "
              "tests; the pydantic-settings leak reddening config nodes). The rule + both symptoms fit the "
              "budget. The other two members of the family are NOT merged (see CL-04b)."),
   surprise=None),

 dict(cid="CL-04b", rank=None,
   title="ENSEMBLE_AUTH_* family - the two members that cannot ride along",
   members=["74245f23-8e20-4ec3-87ea-99bedd7044be", "ceae2f60-5269-43ba-8c5e-82ecb5ab874d"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. auth-lane-scrub-ensemble-auth-env adds a SECOND symptom surface (the scratch-bind "
              "preflight refusing with exit 78, payload shipping auth.enabled:false) and the 7-vars count; "
              "gate-langcheck-auth-scrub-false-negative is a COUNTER-rule (do NOT scrub ISSUER for language_check "
              "because Config() requires it; scrub only for scope-cage route-census files). Merging those two "
              "into the CL-04 body would drop named durable facts."),
   dropped=["the scratch-bind preflight exit-78 refusal + payload ships auth.enabled:false (auth-lane-scrub-ensemble-auth-env)",
            "the ENSEMBLE_AUTH_* 7-vars count (auth-lane-scrub-ensemble-auth-env)",
            "the language_check EXCEPTION and its scrub-only-for-scope-cage-route-census-files scoping (gate-langcheck-auth-scrub-false-negative)",
            "the Config()-requires-issuer rationale and the 2 false-negative failures count (gate-langcheck-auth-scrub-false-negative)"],
   surprise=None),

 dict(cid="CL-05", rank=5,
   title="Mutation proof must attack the pins OWN property (2-member core)",
   members=["b2d5169e-e5bf-4d9f-9a14-d8175becfe6b", "e9d9e684-47c7-472e-a1bb-f6ab43702f47"],
   survivor="mutation-proof-must-target-pin-property",
   required=["immune to a defect class", "cannot redden", "SPEC is inverted", "proof lists are inputs",
             "re-derive the spec", "non-biting plant"],
   variants=[
     "A pin deliberately immune to a defect class cannot redden if that class is removed - the proof SPEC is inverted, not the pin weak. Commissioned proof lists are inputs, not ground truth: re-derive the spec, dont paper over a non-biting plant.",
     "A pin deliberately immune to a defect class cannot redden if that class is removed - the proof SPEC is inverted, not a weak pin. Commissioned proof lists are inputs, not ground truth: re-derive the spec, dont paper over a non-biting plant.",
   ],
   routed_out=[],
   rationale=("Both members are the same inverted-proof-spec rule: a repaired pin that is deliberately immune to "
              "a defect class cannot redden when that class is removed, so the SPEC is inverted - and a "
              "commissioned proof list is an input, not ground truth. One row carries both halves."),
   surprise=None),

 dict(cid="CL-05b", rank=None,
   title="Mutation proof - the third member that does not fit",
   members=["887dc716-4cd2-4366-b3c0-e2bc41a25e47"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE into CL-05. mutation-01-guards-need-counter-mutation adds two durable facts the "
              "CL-05 body cannot also carry inside 240 chars: the counter-mutation framing itself (a test green "
              "under its own legs revert is not vacuous if a COUNTER-mutation reddens it) and the mutation "
              "failures: 1 is signal, not flake clause. Option for PM: keep this row as the counter-mutation "
              "framing row and let CL-05 own the inverted-spec rule - the two are related, not identical."),
   dropped=["a test green under its own legs revert is not vacuous if a COUNTER-mutation reddens it (mutation-01-guards-need-counter-mutation)",
            "absence-only and raw-text pins are prose-satisfiable - re-aim at the POSITIVE shape (mutation-01-guards-need-counter-mutation)",
            "mutation failures: 1 is signal, not flake (mutation-01-guards-need-counter-mutation)"],
   surprise=None),

 dict(cid="CL-06", rank=None,
   title="Census pins must be lane-scoped (3 near-identical rows)",
   members=["a883b76b-77cd-45b0-b9d0-b0c9acd8bc24", "67d72ca9-1eaf-4a23-af6c-2fa2388db0d9",
            "970a600e-f6f1-4a3c-b346-1fd37c565870"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. Three rows assert the same lane-scoped census-pin rule from three angles: the "
              "GENERAL form (census-pin-lane-ownership-form), the WORKED INSTANCE (memoir-census-pin-lane-scoped), "
              "and the MUTATION PROOF that the instance pin actually bites (memoir-census-pin-biting-mutations). "
              "The union is ~415 chars of durable fact - it does not fit 240."),
   dropped=["the global equality re-breaks on every later lane consequence clause",
            "the two biting mutations (lane own migration file removed from the working-tree delta -> RED; lane commit range re-pointed at a foreign-lane commit -> RED) and their byte-identical restore",
            "the delta is exactly one file over LANE_BASE_COMMIT..LANE_COMMIT precision"],
   surprise=("Highest-value merge in the corpus by fact redundancy (3 rows, 1 rule) and the one the 240-char "
             "cap most visibly blocks. PM may prefer a SPLIT (general rule row + worked-instance row) over a "
             "merge - that de-duplicates the rule without losing a fact, unlike a merge.")),

 dict(cid="CL-07", rank=None,
   title="Hand-back halt: root cause + self-resolution",
   members=["0d372d77-a2b7-4f4c-a299-59985c560bd5", "82491ef9-c31f-4bf7-9f9d-5e0bb1ee808f"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~332 chars) exceeds the cap. The two members split the episode: "
              "promote-01 gives the ROOT CAUSE (_gate_serving_attribution Layer 1 needs every 9797 LISTEN holder "
              "argv-owned, but daemon children inherit the listen fd so it always refuses) and promote-02 gives "
              "the SELF-RESOLUTION + watch recipe (serving_green_commit + sweep closes pending_op ~9-11min later; "
              "watch state.json history tail + GET livez|readyz; no restart, counter untouched)."),
   dropped=["the no restart, counter untouched observation (promote-02-handback-halt-self-resolve)",
            "the watch recipe state.json history tail + GET livez|readyz (promote-02-handback-halt-self-resolve)",
            "the probe rc=3 vs 5s budget probe signature (promote-02-handback-halt-self-resolve)"],
   surprise=None),

 dict(cid="CL-08", rank=6,
   title="skill_ab_disposition_manifest: zero consumers + withdrawn claims",
   members=["b0611691-7a2e-4756-95ad-882a23131455", "e22957fd-6b5d-43ff-b486-2dead9206b69"],
   survivor="skillab-manifest-01-no-consumer",
   required=["ZERO daemon consumers", "newer-gen default", "do NOT auto-divert", "named evidence id",
             "never blanket-apply"],
   variants=[
     "skill_ab_disposition_manifest: ZERO daemon consumers (data-only) - the sweep uses the plain newer-gen default and declared pairs do NOT auto-divert; the pin enforces a named evidence id per entry, never blanket-apply.",
   ],
   routed_out=[
     "3 of 6 resolved 2026-11-18 / 3 remain open, crossing the 7d gate 2026-11-23 ~08:00-09:21Z - ROLLING STATE. It must be routed to the KV / board BEFORE the members are archived, or the open deadline is lost.",
     "3 of 6 verified-defective claims withdrawn (rows 8f8cee3a, b77128c5, 1b8e4d80 - no finding) - dated inventory of the manifest artifact; the manifest + its evidence-id pin live in the repo.",
   ],
   rationale=("Two members, one subject: the manifest is data-only with no daemon consumer, the sweep uses the "
              "plain newer-gen default (declared pairs do not auto-divert), and the pin enforces a named "
              "evidence id per entry and is never blanket-applied. Everything durable fits."),
   surprise=None),

 dict(cid="CL-09", rank=7,
   title="snapshot-l3 red-set shape: 2 detectors + 4 invariance guards",
   members=["c2915561-aae8-49a1-af92-eb1247c05814", "65759d88-be5d-4524-9fc4-8804931cce30"],
   survivor="verify-02-snapshot-l3-red-set",
   required=["6 new tests", "2 are red-at-base by design", "LEG A", "reranked[:limit]", "LEG B",
             "stage2_score", "4 are green-at-base invariance guards", "All 6 red is WRONG",
             "FALSE comment", "lost coverage"],
   variants=[
     "snapshot-l3: of 6 new tests exactly 2 are red-at-base by design (LEG A malformed stage-3 -> reranked[:limit]; LEG B prompt stage2_score); 4 are green-at-base invariance guards. All 6 red is WRONG; a deleted FALSE comment != lost coverage.",
   ],
   routed_out=[
     "The named list of the 4 green-at-base guards (declined-stays-empty, selection-follows-pick, exception-path baseline, prompt-window + JSON contract) - inventory of a landed test set; the tests live in the snapshot-l3 lane test files.",
     "snapshot-l3-02-base-red-set-one (d31fa038) is NOT merged: its content is a dated base-leg baseline (base 818411107, 172 nodes / 3 files, suite red set = exactly 1) - see the completion scan.",
   ],
   rationale=("Both members state the same 2-vs-4 split of the 6 new snapshot-l3 tests from opposite sides, and "
              "both carry the anti-misreading clause (expecting all 6 red is wrong; a deleted FALSE comment is "
              "not lost coverage). One row carries it."),
   surprise=None),

 dict(cid="CL-10", rank=8,
   title="gc P3: pin the FILTER side (cursor-pre-advance blind spot)",
   members=["9e76cac1-2282-4752-b11c-4e51611247b3", "f8a54e0f-a551-4b85-9e70-1d88d647efc6"],
   survivor="gc-gate-03-filter-side-pin",
   required=["FILTER side", "over-inclusion by design", "boot_deliver never called the tag resolver",
             "cursor-pre-advance", "over-inclusion by design", "over-inclusion defects", "No-workaround pin",
             "resolve targets before CAS", "seed a non-target member"],
   variants=[
     "pin the FILTER side on over-inclusion by design - boot_deliver never called the tag resolver; cursor-pre-advance fixtures hide over-inclusion defects. No-workaround pin: resolve targets before CAS/cursor advance; seed a non-target member.",
   ],
   routed_out=[],
   rationale=("Two members, one lesson: when the substrate over-includes by design, pin the FILTER side - "
              "boot_deliver never called the tag resolver and cursor-pre-advance fixtures hid the defect, so "
              "the pin must resolve targets before CAS/cursor advance and seed a non-target member."),
   surprise=None),

 dict(cid="CL-11", rank=None,
   title="Release / stage order (bump -> tag -> stage)",
   members=["063d3d90-a211-4b19-a980-1f68b32b2cd3", "7079c358-d0dd-41ff-ad89-08f6df7da724"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~370 chars) exceeds the cap: release-01 carries the release chain "
              "(bump commit -> ANNOTATED tag -> stage worktree DETACHED at tag -> FE REBUILD -> sidecar re-stamp "
              "-> stage.sh, tag-before-stage mandatory via git describe --exact-match) and land-02 carries the "
              "tip-identity definition (_tip_sha reads refs/remotes/origin/latest; tip-identity = tag exists + HEAD "
              "== tagged commit; merge, push latest, push tag, THEN stage from a clean dedicated worktree; e2e "
              "runs post-push)."),
   dropped=["the release chain (bump -> ANNOTATED tag -> DETACHED stage worktree -> FE REBUILD -> sidecar re-stamp -> stage.sh)",
            "stage.sh _tip_sha reads refs/remotes/origin/latest + e2e runs post-push",
            "git describe --exact-match HEAD == VERSION as the tag-before-stage proof"],
   surprise=None),

 dict(cid="CL-12", rank=None,
   title="prompt-assent: gate owner + Discord-first mint context",
   members=["f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d", "6cdf61f7-a340-4624-8db8-9342c6a377dc"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~280 chars) exceeds the cap. f91f7c0b gives the owner triple (owner "
              "daemon/tools/prompt_assent.py, window manager._user_origin_windows, mint context = the window "
              "message rows nested metadata['discord']); 6cdf61f7 gives the arming asymmetry (an api turn arms "
              "the factor-2 window; mint/approve need is_dm/is_group_dm from that metadata or refuse "
              "mint-requires-private-channel)."),
   dropped=["the refusal string mint-requires-private-channel",
            "an api turn ARMS the factor-2 window (the api-vs-discord asymmetry)",
            "the is_dm/is_group_dm field pair as the mint precondition"],
   surprise=None),

 dict(cid="CL-13", rank=9,
   title="Port resolution: daemon PORT source + the 9798 metrics knob (a CORRECTION merge)",
   members=["3b219d3e-c497-4382-a3bb-f3a2bb67e48f", "22e0fb94-e287-4edc-a1ea-36b7aafc298b"],
   survivor="portfence-01-daemon-port-comes-from-port-env",
   required=["${PORT:-8088}", "NOT DAEMON_PORT", "ambient PORT binds the live port",
             "9798", "NOT covered by PORT/DAEMON_PORT", "LIMITS_METRICS_LISTENER_PORT", "staged binary"],
   variants=[
     "daemon.port resolves from config.yaml ${PORT:-8088}, NOT DAEMON_PORT - ambient PORT binds the live port. MetricsListener 9798 is NOT covered by PORT/DAEMON_PORT but IS fenceable via LIMITS_METRICS_LISTENER_PORT (proven on staged binary).",
     "daemon.port resolves from config.yaml ${PORT:-8088}, NOT DAEMON_PORT - ambient PORT binds the live port. MetricsListener 9798 is NOT covered by PORT/DAEMON_PORT but IS fenceable via LIMITS_METRICS_LISTENER_PORT (proven on a staged binary).",
   ],
   routed_out=[
     "anomaly 07e8410c (the record of the original not-fenced-at-all claim) - lives in the anomaly log; the claim itself is CORRECTED by the merged body.",
     "the control-boot detail (a boot moved the metrics listener to 19050 cleanly) - dated proof artifact; proven on the staged binary retains the anchor.",
   ],
   rationale=("This is a CORRECTION merge, not just a redundancy merge: portfence-01 asserts the "
              "MetricsListener 9798 is not fenced at all, which portfence-03 falsifies - it IS runtime-fenceable "
              "via LIMITS_METRICS_LISTENER_PORT, merely unfenced BY DEFAULT and outside PORT/DAEMON_PORT. "
              "Merging removes a live contradiction from the store."),
   surprise=None),

 dict(cid="CL-14", rank=10,
   title="Todo-tool carrier state + the case-sensitive reminder-pin caution",
   members=["8ebed7dc-69fc-4c73-a6e1-95b3d0903234", "a3020315-8141-4552-aaba-3f351e5a27ee"],
   survivor="gc-p4-03-note-drop-turn-end-reminder",
   required=["all 5 todo tool paths", "6 reminder methods", "add_subtask = manager-only, no tool",
             "CASE-SENSITIVE", "docstring scan", "ESCAPES"],
   variants=[
     "Carrier now on all 5 todo tool paths; manager has 6 reminder methods (add_subtask = manager-only, no tool). Auditor caution: the pin no-gap half is a CASE-SENSITIVE reminder docstring scan - a new reminder method omitting it ESCAPES.",
     "Carrier now on all 5 todo tool paths; manager has 6 reminder methods (add_subtask = manager-only, no tool). Auditor caution: the inventory pin no-gap half is a CASE-SENSITIVE reminder docstring scan - a new reminder method omitting it ESCAPES.",
   ],
   routed_out=[
     "Both FIXED by c249d7fce (2026-11-16) episode markers (the 2-path carrier gap and the add/remove_subtask drop) - dated episode; the fix lives in git history c249d7fce.",
     "SUPERSEDED 2026-11-16 episode markers - dated; same git history.",
   ],
   rationale=("Both members are the same remediation episode (both closed by c249d7fce, 2026-11-16) and carry "
              "one shared durable residue: the current carrier state plus the auditor caution that the "
              "inventory pin no-gap half is a CASE-SENSITIVE reminder docstring scan. The episode markers "
              "live in git history; the residue must survive in the merged row."),
   surprise=("Both slugs are named after the CLOSED episode (slot-gap, note-drop). Reusing either slug as "
             "the survivor therefore leaves an episode-named row holding live state. A neutral successor slug "
             "(e.g. gc-p4-reminder-carrier-current-state) is cleaner but adds a slug - PM call.")),

 dict(cid="CL-15", rank=None,
   title="Unit-shard gate fences (3 near-identical rows)",
   members=["5ddf201c-4879-478b-a759-5e6d909237f3", "1cd099e4-3df3-4715-9cfd-f84a4df588cb",
            "2f2aca22-1778-48a4-af5e-77ed24e324e8"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. Three rows state the unit-shard fence; the union exceeds 240. The overlap core is "
              "the exclusion set + the pytest.exit HARD-ABORT condition on POSTGRES_DB, but each member adds a "
              "distinct durable fact."),
   dropped=["junitxml xunit2 lacks file attr: re-derive ids tree-anchored (gc-gate-fence-family-and-xunit2-digest-trap)",
            "Ambient ENSEMBLE_AUTH_* leaks past the task fence (both legs) (gc-gate-fence-family-and-xunit2-digest-trap)",
            "isolate each of the 5 shard files (RC=1) or xdist dies RC=3 (gc-gate-fence-family-and-xunit2-digest-trap)",
            "ensemble_dev aborts EXCEPT the prompt-cache fence (allows ensemble_dev) (testenv-01-unit-shard-hard-abort)",
            "scrub POSTGRES_* PER-VARIABLE, pin nothing (testenv-01-unit-shard-hard-abort)"],
   surprise=None),

 dict(cid="CL-16", rank=None,
   title="Zero-traffic skill A/B stall (3 near-identical rows)",
   members=["07df8535-8389-4f53-9d50-86b683b73864", "63f64bbe-4f96-45a7-860e-bafb07aa7fca",
            "af8815b5-5623-439d-97d7-973bfe7a5c30"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~310 chars) exceeds the cap. skillab-01 carries the service anchors "
              "(check_ab_test_resolution returns needs_more_data at skill_evolution_service.py:1462, BEFORE the "
              "extension_count bump at :1536; both gens stay is_active); skillab-02 carries the unimplemented "
              "doctrine + the keeper remedy; skillab-03 carries the counter semantics (comparisons bumps ONLY on "
              "genuine injections via _select_ab_variant->increment_comparison; record_feedback on-miss rows do "
              "NOT)."),
   dropped=["the service anchors skill_evolution_service.py:1462 (needs_more_data) and :1536 (extension_count bump) + both gens stay is_active",
            "the keeper remedy (skill_feedback note + deferred skill_fix RE-TARGET; report stall)",
            "comparisons bumps ONLY on genuine injections (_select_ab_variant->increment_comparison); record_feedback on-miss rows do NOT"],
   surprise=None),

 dict(cid="CL-17", rank=None,
   title="P11 drift-guard marker requirement + cross-lane carve-out",
   members=["b93a2b9a-72b0-403d-8f9b-2d3f9b316fd4", "1a88b07f-1f0e-497d-8383-6f97ff4589bb"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~400 chars) exceeds the cap by a wide margin: source-gc-drift-guards-"
              "branch-tip-sensitive carries the sanctioned-marker list (decision, memoir, group chat), the "
              "COMMITTED-span-only rule and the per-hunk marker fix; source-gc-02-cross-lane-carve-out-cure "
              "carries the SANCTIONED_LANE_MARKERS carve-out (7bc6bff74) and the TEMP COMMIT + soft reset "
              "counter-mutation method."),
   dropped=["the sanctioned marker tuple (decision, memoir, group chat) + guard diffs COMMITTED span (working-tree blind)",
            "the SANCTIONED_LANE_MARKERS carve-out and cure sha 7bc6bff74",
            "the TEMP COMMIT + soft reset counter-mutation method"],
   surprise=None),

 dict(cid="CL-18", rank=None,
   title="Seal-map self-consistency + the stale QUARANTINE row",
   members=["3087ee94-3800-49f9-8748-4f81f1c4432d", "ffe47734-be35-4088-83d1-e4a45a0df0c0"],
   survivor=None, required=[], variants=[], routed_out=[],
   rationale=("DO-NOT-MERGE. The union (~300 chars) exceeds the cap. Both members assert that the seal map is "
              "self-consistent at base 818411107 (35/35) and that a red proven at an older base (1fecf7c3f) is "
              "not attributable to later lanes; quarantine-01 additionally states that the QUARANTINE.md "
              "chart stale-seals x1 row is stale."),
   dropped=["the QUARANTINE.md chart stale-seals x1 row verdict + repaired by the 2026-11-05/07 re-pins",
            "35/35 seal rows match as the base-map census",
            "HEAD-leg divergence is lane-introduced"],
   surprise=None),
]

SCAN = {
 "77bb3c69-bb35-42d3-b81e-5ff643847146": ("K", "", "Keyword closed comes from fail-closed (a mechanism descriptor). The rows subject is OPEN: skill-keeper lacks meta.json skill_injection so its A/B group can never resolve."),
 "a883b76b-77cd-45b0-b9d0-b0c9acd8bc24": ("K", "", "Keyword closed comes from the lane's closed commit range - a range qualifier, not a self-closing marker. Row stays active (also a CL-06 member)."),
 "d00581d4-a4ad-4613-a0f5-136927f5c6d2": ("K", "", "Keyword closed comes from fail closed on mismatch - fail-closed semantics, not self-closing. The kill-channel rule is live."),
 "bed25def-a69b-489a-9f62-9966b3208b24": ("K", "", "superseded describes the stage-v0296 RELEASE, not the rows subject. The rule (superseded stage trees may be removed even if UNMERGED) is live."),
 "ce03ba0c-3ade-450b-8aed-4d0bc26497a8": ("c", "", "Half-closed: the step-0 fix (9b59bafcd) is closed, but the row carries an OPEN blocker - no scratch DB (ensemble_dev_app rolcreatedb=FALSE; project_future exits 3; needs a superuser scratch). JUDGMENT-NEEDED: archive the closed half only after a successor carries the blocker."),
 "28889a8b-1234-4f28-88c6-6bce224d12ec": ("c", "", "The A/B is resolved, but the row carries an OPEN obligation (OWED: DB description still pre-FIX text; retire 9a4a1f54 once gen-2 wins). JUDGMENT-NEEDED - the obligation must survive in an active row."),
 "f8a54e0f-a551-4b85-9e70-1d88d647efc6": ("K", "", "so it shipped = the defect shipped. The lesson is live (also a CL-10 member)."),
 "a3020315-8141-4552-aaba-3f351e5a27ee": ("b", "git history c249d7fce", "SELF-CLOSING: the add/remove_subtask drop is FIXED (c249d7fce) - the episode now lives in git history. Its durable residue (the CASE-SENSITIVE reminder docstring-scan caution) is carried by the CL-14 merged body, so archival is justified IF CL-14 lands first."),
 "ccf336d0-2ff7-4764-a062-8666083e0bfc": ("K", "", "the only complete red set - complete as an adjective. The junitxml-is-the-red-source rule is live."),
 "30291831-3763-4e6a-9df4-a4938e0038dc": ("b", "release tree + git history 56a5f4a00 + the group-chat plan corpus", "Dated snapshot: Group-chat Phase-2 COMPLETE @ 56a5f4a00 with the shipped phase inventory (room store + migration 131, naming cascade, delivery lanes, nudges, tool #23 + joined-room block, UI modal + GC) now living in the release tree / git history / plan corpus. CAUTION: the Next: staged release lane; live promote = Emperors clause is REFRESHABLE-OVERDUE rolling state - route it to KV/board BEFORE archival."),
 "e71966f5-75fa-49f6-ba95-01768d3afaba": ("K", "", "committed docs carry PRE-ratification archived design - archived describes the DOCS design, not the rows subject. The row is a LIVE obligation (the merge MUST land those 4 docs)."),
 "8ebed7dc-69fc-4c73-a6e1-95b3d0903234": ("b", "git history c249d7fce", "SELF-CLOSING: the 2-path carrier gap is FIXED (c249d7fce). Durable residue (carrier now on all 5 todo tool paths; manager has 6 reminder methods) is carried by the CL-14 merged body - archival justified IF CL-14 lands first."),
 "3fbd3865-1fa9-45cc-8a85-eb04f3ad450c": ("K", "", "rewrite the COMPLETE object - complete as an adjective. The set_kv replace-semantics rule is live."),
 "7c9b9fa5-f991-4d79-bcc5-92156d3cd50b": ("K", "", "a plan's ledger can assert FIXED - FIXED describes a ledger CLAIM, not the rows subject. The body-vs-ledger rule is live."),
 "6db7e0fd-a716-4463-8e5f-bfea1a118c87": ("K", "", "fails closed AT BOOT - fail-closed semantics. NOTE: this row is a phase-2 MERGE SURVIVOR (survives prompt-assent-flip-on-gate) - do not touch."),
 "6f44fde8-da8c-41b5-b55f-ed0ec4da66e4": ("c", "", "The 402 episode is closed (failover recovered 08:13Z), but the row carries an OPEN obligation (wallet still owed) plus a durable mechanism fact (provider 402 insufficient_balance -> turns land status=error, no auto-retry) that no other active row carries. JUDGMENT-NEEDED."),
 "b43c6db1-91cd-429e-ae41-3d1f3f2d065f": ("c", "", "The latent P2 defect is fixed, but the durable GOOTCHA (from-imports cannot reach class attributes; use RoomRepository._raise_typed_integrity) is carried by no other active row. JUDGMENT-NEEDED - successor row with the gotcha, then archive."),
 "bdfb7de6-1fbb-4c4b-8217-42c3389a73a1": ("K", "", "shipped lanes describes shipped surfaces, not the rows subject. The shared-helper blast-radius rule is live."),
 "783a1451-b41b-47c9-9e5f-1152480a2109": ("a", "bdfb7de6-1fbb-4c4b-8217-42c3389a73a1 (review-01-shared-helper-blast-radius)", "The durable rule it carries (shared-helper blast radius; classify shipped vs new; disclose shipped-copy changes as a deviation entry, never as a lane-local fix) is covered by the ACTIVE row named here. Its residual content (describeHttpError's 3 call sites) is a dated inventory of one PR - lives in git history."),
 "d5fde27d-8a2f-477e-b400-4faf1edb6216": ("K", "", "resolved is a METRIC noun (0 sweep-resolved); the rows subject is UNMET (resolve AT PROMOTE not satisfied). Keep active."),
 "0df359fe-4a6b-4c93-9cae-816798a97ee5": ("K", "", "resolutions landed - landed as a verb. The row records a gauge that has NOT collapsed. Also rolling state (watch #7/#8 counts) - a routing candidate for KV/board, but not a completion marker."),
 "83a4b2d3-dd41-4f85-b799-11db50aefaee": ("K", "", "Verify the restart landed - landed as a verb. The /proc/<pid>/environ verification recipe is live."),
 "63f64bbe-4f96-45a7-860e-bafb07aa7fca": ("K", "", "deferred skill_fix RE-TARGET - deferred describes the deferral MECHANISM, not a closed episode. Keep active (also a CL-16 member)."),
 "af8815b5-5623-439d-97d7-973bfe7a5c30": ("K", "", "re-run deferred FIX manually - deferred describes the FIX, not a closed episode. Keep active (also a CL-16 member)."),
 "b0611691-7a2e-4756-95ad-882a23131455": ("K", "", "3 of 6 resolved but 3 remain open, crossing the 7d gate 2026-11-23 - the subject is NOT closed. Keep active; the open deadline must be routed to KV/board (also a CL-08 member)."),
 "ebe4f878-ac79-4d9e-8ccb-ceebedfa1aa4": ("a", "700bd23b-e8b9-4f12-9858-1b133cae1047 (sonic-02-complexity-routing-doctrine)", "SELF-CLOSING: the Daemon work never routes to sonic + [sonic-blast-radius-v1] gate is SUPERSEDED/REMOVED by Ruling A (staged v0.30.5, feature/sonic-routing-v0305). Its durable replacement rule (Quick core-file changes are legitimate sonic work; routing is complexity-only) is covered by the ACTIVE row named here; the episode lives in the Ruling."),
 "82a3ca1c-07ad-4fc9-9ca6-6bbd65e97b06": ("K", "", "Routed (not fixed in the bind-race lane) - fixed inside a NEGATION. The subject is an OPEN defect (TypeError every ~10s at daemon/services/metrics_query/wiring/w3.py:234,240, keeping F18 dark). Keep active."),
 "3087ee94-3800-49f9-8748-4f81f1c4432d": ("K", "", "Row was true only at its own base 1fecf7c3f - past-tense scope, not a self-closing marker. The row is a live CORRECTION of a stale QUARANTINE row."),
 "df5f8463-318a-4373-8c79-5f5bc102cfba": ("K", "", "tagging only the final - final as an adjective. The bump_version self-commit rule is live."),
}

KW = [("CLOSED", r"\bclosed\b"), ("RESOLVED", r"\bresolved\b"), ("DEFERRED", r"\bdeferred\b"),
      ("RETIRED", r"\bretired\b"), ("SUPERSEDED", r"\bsuperseded\b"), ("FIXED", r"\bfixed\b"),
      ("LANDED", r"\blanded\b"), ("DONE", r"\bdone\b"), ("COMPLETE", r"\bcomplete\b"),
      ("SHIPPED", r"\bshipped\b"), ("ARCHIVED", r"\barchived\b"), ("DISSOLVED", r"\bdissolved\b"),
      ("NO-LONGER", r"\bno longer\b"), ("FINAL", r"\bfinal\b"),
      ("WAS-TRUE-ONLY", r"\bwas true only\b")]

hits = []
for r in ROWS:
    low = r["body"].lower()
    ks = [k for k, p in KW if re.search(p, low)]
    if ks:
        hits.append((r, ks))
missing = [r["id"] for r, _ in hits if r["id"] not in SCAN]
assert not missing, "un-adjudicated keyword hits: %s" % missing
unknown = [i for i in SCAN if i not in BY]
assert not unknown, "SCAN ids not in listing: %s" % unknown

def family(slug):
    m = re.match(r"^(.*?)-(\d{2})-(.+)$", slug)
    if m:
        return m.group(1)
    return slug.split("-")[0] if "-" in slug else slug

fam = collections.defaultdict(list)
for r in ROWS:
    fam[family(r["slug"])].append(r)
over_cap = sorted([(f, len(v)) for f, v in fam.items() if len(v) > 3], key=lambda x: (-x[1], x[0]))

def pick_body(cl):
    if not cl["variants"]:
        return "", 0, False, []
    best = None
    for v in cl["variants"]:
        miss = [p for p in cl["required"] if p not in v]
        fits = len(v) <= CAP
        if best is None or len(v) < len(best[0]):
            best = (v, miss, fits)
        if fits and not miss:
            return v, len(v), True, []
    return best[0], len(best[0]), False, best[1]

resolved = []
for cl in CLUSTERS:
    body, n, ok, miss = pick_body(cl)
    cl["_body"], cl["_len"], cl["_ok"], cl["_miss"] = body, n, ok, miss
    resolved.append(cl)

YES = [c for c in resolved if c["_ok"]]
NO = [c for c in resolved if not c["_ok"]]
rows_freed = sum(len(c["members"]) - 1 for c in YES)
covered_members = {m for c in resolved for m in c["members"]}

def hdr(title):
    return """# {title}

**Input listing (authoritative, exact-at-capture):** `{listing}`
**Listing capture time:** {cap} - **rows:** {n} (active only) - **whole-file sha256:** `{lsha}`
**Bodies aggregate (declared == recomputed):** `{agg}` - **per-row body sha256:** 277/277 verified
**Status filter:** `{filt}` - **project:** `{proj}`

> **DRIFT CAVEAT.** {drift} Anything inserted after 15:25:26Z is NOT in this analysis; PM should re-pull the listing before executing a merge whose member set could have changed.

**Generation method (machine-generated, not hand-typed).** A python script reads the listing JSON and emits this file; every member id, store slug and member body below is COPIED MACHINE-SIDE from the JSON (no retyping anywhere in the pipeline). Merged-body char counts are computed programmatically and every merged body is asserted <= {capn} chars. Required durable-fact phrases are asserted present as literal substrings before a merge is proposed.

**Durable-fact rubric (used for the COVERAGE verdict).** A member's content is a DURABLE FACT if it is a reusable operational assertion - a mechanism, a trap, a rule, a protocol, a code/file anchor, an env var or an exact string. It is NOT durable if it is a dated snapshot (counts, node totals, as-of-date), an inventory of a landed artifact's contents (test names, file lists, key fingerprints), a one-time incident outcome, or a live-status/rolling value (deadlines, open-queue counts). COVERAGE = YES requires the merged body to be <= {capn} chars AND to carry every member's durable fact. Content deliberately not carried is listed per cluster as ROUTED OUT with the home where it lives - PM must confirm that home before archiving the member.

**Guardrail (unchanged from the sweep charter).** A row may be archived ONLY if (a) the durable fact is covered by another ACTIVE row, or (b) it is a dated snapshot whose content now lives in a routed home. NEVER on age, NEVER on access_count. Merges here are proposals for PM; this lane performed NO POK writes.
""".format(title=title, listing=LISTING, cap=doc["generated_at"], n=doc["active_count"],
           lsha=LISTING_SHA, agg=agg, filt=doc["status_filter"], proj=doc["project_id"],
           drift=DRIFT, capn=CAP)

out = [hdr("POK sweep wave 2, lane B - CLUSTER DERIVATION (2026-11-18)")]
out.append("""
## 1. Result summary

| Metric | Value |
|---|---|
| Active rows analysed | {n} |
| Candidate clusters adjudicated | {nc} |
| Clusters with COVERAGE = YES (merge proposed) | {ny} |
| Clusters with COVERAGE = NO (DO-NOT-MERGE) | {nn} |
| Rows freed if all YES clusters land | {rf} |
| Active rows NOT in any cluster | {notc} |

**Yield-ranked merge list**

| Rank | Cluster | Survivor slug | Members | Rows freed | Merged body chars | Coverage |
|---|---|---|---|---|---|---|
""".format(n=len(ROWS), nc=len(resolved), ny=len(YES), nn=len(NO), rf=rows_freed,
           notc=len(ROWS) - len(covered_members)))
for c in sorted(YES, key=lambda x: x["rank"]):
    out.append("| {rank} | {cid} - {title} | `{surv}` | {nm} | {freed} | {ln} | YES |".format(
        rank=c["rank"], cid=c["cid"], title=c["title"], surv=c["survivor"],
        nm=len(c["members"]), freed=len(c["members"])-1, ln=c["_len"]))
out.append("""
**DO-NOT-MERGE clusters (blocked by the {cap}-char cap, not by lack of redundancy):** {ids}
""".format(cap=CAP, ids=", ".join(c["cid"] for c in NO)))

out.append("\n## 2. Clusters with COVERAGE = YES (merge proposed)\n")
for c in sorted(YES, key=lambda x: x["rank"]):
    out.append("""
### {cid} (rank {rank}) - {title}

- **Survivor slug:** `{surv}` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** {nm} - **rows freed:** {freed} - **merged body length:** {ln}/{cap} chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= {cap} chars and every member's durable fact is carried (all {nreq} required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**
""".format(cid=c["cid"], rank=c["rank"], title=c["title"], surv=c["survivor"], nm=len(c["members"]),
           freed=len(c["members"])-1, ln=c["_len"], cap=CAP, nreq=len(c["required"])))
    for m in c["members"]:
        r = BY[m]
        out.append("""
- **`{slug}`** - id `{id}` - domain `{dom}` / audience `{aud}` - created `{ca}` - body sha256 `{sha}`
```
{body}
```
""".format(slug=r["slug"], id=r["id"], dom=r["domain"], aud=r["audience"], ca=r["created_at"],
           sha=r["content_sha256"], body=r["body"]))
    out.append("""
**Proposed merged body ({ln}/{cap} chars)**
```
{body}
```
**Why this merge:** {rat}
""".format(ln=c["_len"], cap=CAP, body=c["_body"], rat=c["rationale"]))
    if c["routed_out"]:
        out.append("**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**\n")
        for x in c["routed_out"]:
            out.append("- " + x)
        out.append("")
    if c.get("surprise"):
        out.append("> **NOTE.** " + c["surprise"] + "\n")

out.append("""
## 3. Clusters with COVERAGE = NO (DO-NOT-MERGE)

Each cluster below is genuinely redundant - the members assert the same fact from different angles - but the union of their durable facts does not fit {cap} chars. **Never drop a fact to fit the limit:** the fact the limit would drop is named for each. PM must not merge these; if the redundancy must be reduced, the durable fact has to survive in an active row first (a successor row, or a split of the cluster), not be discarded.
""".format(cap=CAP))
for c in NO:
    out.append("""
### {cid} - {title}

- **Members:** {nm} - **survivor slug:** {surv} - **COVERAGE VERDICT: NO - DO-NOT-MERGE**
""".format(cid=c["cid"], title=c["title"], nm=len(c["members"]),
           surv=("`" + c["survivor"] + "`") if c.get("survivor") else "n/a (no merge proposed)"))
    for m in c["members"]:
        r = BY[m]
        out.append("""
- **`{slug}`** - id `{id}` - domain `{dom}` / audience `{aud}` - body sha256 `{sha}`
```
{body}
```
""".format(slug=r["slug"], id=r["id"], dom=r["domain"], aud=r["audience"],
           sha=r["content_sha256"], body=r["body"]))
    out.append("""
**Why it cannot merge:** {rat}
""".format(rat=c["rationale"]))
    if c.get("dropped"):
        out.append("**Facts the {cap}-char limit would drop (named - do NOT drop them):**\n".format(cap=CAP))
        for x in c["dropped"]:
            out.append("- " + x)
        out.append("")
    if c.get("surprise"):
        out.append("> **NOTE.** " + c["surprise"] + "\n")
    if c["variants"]:
        out.append("""**Closest-fitting body considered ({ln} chars - NOT proposed; it would drop the facts above):**
```
{body}
```
""".format(ln=c["_len"], body=c["_body"]))

out.append("""
## 4. Mergeable-looking families that are NOT (and why)

The similarity pass produces same-family / same-ordinal pairs that look like duplicates but are different facts. These are the traps in this corpus:

| Family / pair | Why it is NOT a merge |
|---|---|
| `flake-01-ab-control-arm-attribution` vs `flake-01-ab-resolution-staticpool-race` | Same family, same ordinal, opposite subjects: the first is an A/B ATTRIBUTION METHOD (run N=30 on both arms, compare failure RATES); the second is a specific concurrency defect (shared StaticPool SQLite conn + asyncio.to_thread at skill_evolution_service.py:1449). Merging would bury a live flake. |
| `gc-p3-boot-pin-file-node-count` vs `gc-p3-testp11-class-identity` | Both are node-count pins (8 nodes in test_room_boot_f1_pin.py vs 6 nodes in test_source_gc_repository.py) but they pin DIFFERENT files and DIFFERENT counts. The shared lesson (disambiguate same-named classes by node count) is already stated inside gc-p3-testp11-class-identity. |
| `verify-02-snapshot-l3-red-set` vs `snapshot-l3-02-base-red-set-one` | Same lane, overlapping words - but the second is a dated BASE-LEG BASELINE (base 818411107, 172 nodes / 3 files, suite red set = exactly 1), i.e. rolling state, not a second copy of the red-set shape. It belongs in the completion scan, not in a merge. |
| `views-*` (5 rows) | The largest coherent family (origin-gate contract, forwarded-veto contract, non-browser Origin spellings, session-bypass lane tip, verify-POST rate limiting). Every row is a DIFFERENT clause of the same gate - the family reads like one merged row but the union is ~1000 chars and each clause is independently load-bearing. |
| `quarantine-*` (3 rows) | All about QUARANTINE.md, but one is this row is STALE, one is rows must name every red node, one is family rows need node mapping - three distinct rules. |
| `promote-01` / `promote-02` | Look like one story (the hand-back halt) and ARE one story - but the union exceeds {cap} (CL-07). |
| `seal-01` / `seal-02` / `seal-03` | The seal family already shows the merge pattern working: phase-2 merged two of these (survivor `seal-02-repin-dict-keeps-base-value`, 5fcb2598). The remaining two are distinct rules. |

**Phase-2 survivors excluded from clustering (per the wave charter):** `5fcb2598 seal-02-repin-dict-keeps-base-value`, `6db7e0fd prompt-assent-flip-on-gate`, `170d495f prompt-assist-04-freshness-leg-second-race` - their absorbed members are already inactive and are NOT in the 277-row listing, so nothing from the phase-2 consolidations is re-proposed here. (6db7e0fd and 170d495f do appear as near-neighbours of prompt-assent rows; those pairings are recorded in CL-12 - none of them clears the cap.)
""".format(cap=CAP))

out.append("""
## 5. Family-cap observations (derived grouping - the service-side family key may differ)

Under the stated derivation (family = the slug prefix before a 2-digit ordinal, else the first hyphen segment), these families hold MORE than 3 active rows, i.e. above the documented POK family cap of 3 active per family:
""")
for f, n in over_cap:
    slugs = sorted(r["slug"] for r in fam[f])
    out.append("- **{f}** ({n} active): ".format(f=f, n=n) + ", ".join("`" + s + "`" for s in slugs))
out.append("""
Every cluster above reduces a family's row count by 1 (or 2). The families still above the cap after the proposed merges are the ones whose members are genuinely different facts - the corpus is wide, not duplicative. PM should treat the cap as a signal to CHECK A FAMILY, not as a merge quota: a family of 5 distinct facts is not a family of 5 copies.
""")

out.append("""
## 6. Execution notes for PM

1. **Order.** Land the YES clusters first; CL-14 and CL-13 are the ones other rows depend on (CL-14's absorbed rows are only archivable after CL-14 lands).
2. **Re-pull before executing.** The store drifted after capture. Re-run the listing pull and confirm the member set is unchanged before writing.
3. **One skill per merge, one row per merge.** A merge writes the survivor row (same slug, new content -> supersede) and archives the absorbed member(s). PM owns every write; this lane wrote none.
4. **Do not merge the NO clusters.** Their named dropped facts are the ones the limit cannot carry.
5. **Watch the routed-out items** in CL-08 (the E-6 7d-gate deadline must reach KV/board before archival) and CL-09 (the 4 green-at-base guard names live in the lane's test files).

*Generated by `_laneB_generate.py` from `{src}` (sha256 `{lsha}`). No database access; no POK writes.*
""".format(src=os.path.basename(LISTING), lsha=LISTING_SHA))

open(OUT_CLUSTER, "w").write("\n".join(out))
print("wrote", OUT_CLUSTER, os.path.getsize(OUT_CLUSTER), "bytes")

dist = collections.Counter(v[0] for v in SCAN.values())
out2 = [hdr("POK sweep wave 2, lane B - PHASE-1 COMPLETION SCAN (2026-11-18)")]
out2.append("""
## 1. Result summary

| Metric | Value |
|---|---|
| Active rows scanned | {n} |
| Rows with >=1 completion keyword | {h} |
| (a) durable fact covered by another ACTIVE row | {a} |
| (b) dated snapshot; content now lives in a routed home | {b} |
| (c) JUDGMENT-NEEDED for PM | {c} |
| (K) keyword hit is NOT a self-closing marker - keep active | {k} |

**Method.** Programmatic keyword scan over all {n} bodies (mission set: closed / resolved / deferred / retired / superseded / fixed / landed; extended set: done / complete / shipped / archived / dissolved / no-longer / final / was-true-only), then MANUAL adjudication of every hit. Verdicts:

- **(a)** the row marks its subject closed AND the durable fact it carries is already held by another ACTIVE row (named below) -> archival justified.
- **(b)** the row marks its subject closed AND it is a dated snapshot whose content now lives in a routed home (named below) -> archival justified.
- **(c)** the row LOOKS closed but still carries durable content or an OPEN obligation with no routed home -> JUDGMENT-NEEDED for PM.
- **(K)** the keyword is NOT a self-closing marker (fail-closed, closed commit range, deferred FIX, landed as a verb, complete as an adjective, a negation) -> keep active.

**False-positive classes found in this corpus (the reason K is the largest bucket):** fail-closed semantics (fails closed AT BOOT, fail closed on mismatch); hyphenated fail-closed; closed commit range; deferred naming the deferral MECHANISM rather than a closed episode; landed / shipped / complete / final as verbs or adjectives; FIXED inside a negation (Routed (not fixed ...)); resolved as a metric noun (0 sweep-resolved); superseded describing a RELEASE rather than the row's subject.

**Guardrail (unchanged).** (a)/(b) only justify archival. NEVER on age, NEVER on access_count. This lane performed NO POK writes and NO archiving - it names the verdict for PM.
""".format(n=len(ROWS), h=len(hits), a=dist["a"], b=dist["b"], c=dist["c"], k=dist["K"]))
out2.append("\n## 2. Hit table\n")
out2.append("| # | id | store slug | keywords | verdict | covered by / routed home |\n|---|---|---|---|---|---|")
for i, (r, ks) in enumerate(sorted(hits, key=lambda x: x[0]["slug"]), 1):
    v, cover, note = SCAN[r["id"]]
    cov = cover.split(" (")[0] if cover else "-"
    out2.append("| {i} | `{id}` | `{slug}` | {ks} | **{v}** | {cov} |".format(
        i=i, id=r["id"], slug=r["slug"], ks=", ".join(ks), v=v, cov=cov))
out2.append("\n## 3. Per-hit adjudication\n")
for i, (r, ks) in enumerate(sorted(hits, key=lambda x: x[0]["slug"]), 1):
    v, cover, note = SCAN[r["id"]]
    out2.append("""
### {i}. `{slug}`

- **id:** `{id}` - **domain:** `{dom}` / **audience:** `{aud}` - **created:** `{ca}` - **body sha256:** `{sha}`
- **keywords matched:** {ks}
- **body (verbatim, machine-copied from the listing JSON):**
```
{body}
```
- **VERDICT: {v}**{cov}
- {note}
""".format(i=i, slug=r["slug"], id=r["id"], dom=r["domain"], aud=r["audience"], ca=r["created_at"],
           sha=r["content_sha256"], ks=", ".join(ks), body=r["body"], v=v,
           cov=(" - " + cover if cover else ""), note=note))
out2.append("""
## 4. What PM should do with this scan

- **{a} rows** (verdict a) may be archived once PM confirms the named covering row is still ACTIVE and carries the fact.
- **{b} rows** (verdict b) may be archived once PM confirms the named home actually holds the content - note the two CL-14-dependent rows (group-chat-p4-01, gc-p4-03): they are only archivable AFTER the CL-14 merged row lands, because that row carries their durable residue.
- **{c} rows** (verdict c) must NOT be archived as-is: each carries either an open obligation (DB description still pre-FIX; retire 9a4a1f54 once gen-2 wins; wallet still owed) or a durable fact no other active row holds (the provider-402 no-auto-retry mechanism; the from-import/staticmethod gotcha; the scratch-DB CREATEDB blocker). PM should write a successor carrying that content first, then archive the episode row.
- **{k} rows** (verdict K) stay active untouched. Three of them (skill-e6-04, skillab-manifest-01, and the group-chat-01 Next clause) are rolling state and are routing candidates for KV / board / release tree under the durable-vs-rolling-state doctrine - that is a routing decision, NOT an archival one.

*Generated by `_laneB_generate.py` from `{src}` (sha256 `{lsha}`). No database access; no POK writes.*
""".format(a=dist["a"], b=dist["b"], c=dist["c"], k=dist["K"], src=os.path.basename(LISTING), lsha=LISTING_SHA))

open(OUT_SCAN, "w").write("\n".join(out2))
print("wrote", OUT_SCAN, os.path.getsize(OUT_SCAN), "bytes")

print("\n=== CLUSTERS ===")
for c in sorted(resolved, key=lambda x: (x["rank"] is None, x["rank"] or 0, x["cid"])):
    print("%-8s %-4s len=%3d members=%d survivor=%s miss=%s" % (
        c["cid"], "YES" if c["_ok"] else "NO", c["_len"], len(c["members"]), c.get("survivor"), c["_miss"]))
print("\nYES=%d NO=%d rows_freed=%d uncovered_rows=%d" % (
    len(YES), len(NO), rows_freed, len(ROWS) - len(covered_members)))
print("SCAN distribution:", dict(dist), "hits=", len(hits))
for f in (OUT_CLUSTER, OUT_SCAN):
    print("%s sha256=%s bytes=%d" % (os.path.basename(f),
          hashlib.sha256(open(f, "rb").read()).hexdigest(), os.path.getsize(f)))
