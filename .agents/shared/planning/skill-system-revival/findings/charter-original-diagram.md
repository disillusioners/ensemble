# Charter-Original Diagram — the Appendix B baseline

**Purpose.** This file preserves the **charter-validated original** of the Appendix B Mermaid diagram in
`../phase0-investigation.md` (§B.3), so that the diagram's diff against its source stays reproducible
**without needing the prod database**.

## Provenance

| Fact | Value |
|---|---|
| What this is | the block **exactly as the Charter agent issued it**, verbatim — including the `BALL` typo and the three-surface `CST` |
| Why it needed preserving | git could supply no baseline: the planning file is **untracked** (`git status --porcelain` → `?? …/phase0-investigation.md`; `git log --all -- <path>` empty; `git ls-files` empty; no stash, no sibling-worktree copy, no other copy on disk) |
| How the original was recovered | **read-only** from the prod daemon's own message store — `message_queue.message_id = 05a884b4-43ec-49fe-b98f-6593fd216a60`, instance `4175a230-baa4-429d-a5ee-237b9ee5149c`, source `internal_agent:9f42eaf8-…` (this commission's tree root), enqueued `2026-11-09 05:35:24Z` — by the Phase-0 round-1 review's TASK-1 worker, inside a `BEGIN READ ONLY` transaction against `ensemble_prod` @ `10.44.0.2:5432`. The mermaid block sat at lines 76–148 (fences) / 77–147 (content) of that message. |
| When it was written into the repo | **2026-11-09**, in review round 3 — after the `/tmp` scratch copy (`/tmp/mermaid-validate-0bXaeR/original_block.mmd`) was identified as the only other holder. A prod-DB deletion would otherwise have destroyed the sole copy of the pre-edit diagram. |
| Content hash | the content-only form's `sha256`, as recorded by the round-1 review = `c139812146dd4f2d84e9eb837076595996927e22752f0fc4ee8cc049cba60fe7`. This file additionally carries the two fence lines, so its own byte count differs by those. |

## The diff against Appendix B — exactly two node-label lines

```diff
@@ -8,7 +8,7 @@
         CG3["skill_execute_capture tool returns a skipped envelope"]
-        CST["absent from live env, repo .env and demo env - lane OFF by ABSENCE of the variable"]
+        CST["absent from repo .env, live .env, live process env and demo process env - lane OFF by ABSENCE of the variable"]
         CSW --> CRES --> CG1 --> CG2 --> CG3
         CSW -.- CST
@@ -43,7 +43,7 @@
     subgraph BRAKE["OPERATIONAL ASYMMETRY - per-call env resolution vs boot-fixed os.environ"]
         direction LR
-        O1["ALL switches in BALL lanes resolve per-call from os.environ"]
+        O1["ALL switches in BOTH lanes resolve per-call from os.environ"]
```

**Everything else is byte-identical:** 25 node ids (identical set), 23 edges (identical list and order — `-->`×16,
`-.-`×4, `-.->`×3), 3 `classDef` directives, 3 `class` assignments, 2 `style` directives, 4 subgraphs with 4 `end`,
7 comments, identical fences, LF-only whitespace with 0 CR / 0 tabs / 0 trailing whitespace, and 71 content lines
in both forms.

> **Do not "correct" the `BALL` typo or the three-surface `CST` in the block below.** They are the charter's
> original, preserved deliberately so the diff above can be re-derived — the Appendix B copy in
> `phase0-investigation.md` is the corrected one, and that file must stay byte-identical to its current state.

## The charter-original block (verbatim)

```mermaid
flowchart TD
    %% CAPTURE LANE (left) - env-only single switch, off by absence
    subgraph CAPTURE["CAPTURE LANE - single switch, env-only, default OFF"]
        direction TB
        CSW["switch ENSEMBLE_SKILL_CAPTURE_ENABLED - env only, NO YAML path, default OFF"]
        CRES["_resolve_switch per-call in daemon/services/skill_job_dispatcher.py: ON 1/true/yes/on, OFF 0/false/no/off, any other token raises ValueError - fail-closed"]
        CG1["gate: SkillMetricsService._check_capture_eligibility"]
        CG2["SkillJobDispatcher.enqueue_capture returns None"]
        CG3["skill_execute_capture tool returns a skipped envelope"]
        CST["absent from live env, repo .env and demo env - lane OFF by ABSENCE of the variable"]
        CSW --> CRES --> CG1 --> CG2 --> CG3
        CSW -.- CST
    end

    %% EVOLUTION LANE (right) - three surfaces, resolved precedence
    subgraph EVOL["EVOLUTION LANE - three switch surfaces, resolved precedence"]
        direction TB
        E1["1 - canonical env ENSEMBLE_SKILL_EVOLUTION_ENABLED (highest)"]
        E2["2 - YAML key skill_evolution.evolution_lane_enabled"]
        E3["3 - alias env SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED"]
        E4["4 - default False"]
        EVN["all three surfaces live on v0.29.7 - field evolution_lane_enabled, no validation_alias, YAML section passed through verbatim"]
        EG1["gate 1 - dispatcher admission gate: refused admission writes NO row"]
        EG2["gate 2 - queued-row execution: terminal refusal at daemon/services/job_processor.py - lane_disabled terminal write"]
        E1 -->|if unset| E2 -->|if unset| E3 -->|if unset| E4
        E4 -->|resolved OFF| EG1 --> EG2
        E1 -.- EVN
    end

    %% OBSERVED FAILURE PATH (top right) - the 2026-10-22 incident
    subgraph INCIDENT["OBSERVED FAILURE PATH - 2026-10-22 incident"]
        direction TB
        K["skill-keeper instance"]
        DJ["capture job dispatched: task_message empty, iterations 0"]
        NT["post-confinement keeper has NO tool to recover the instance messages"]
        KA["keeper attempts edit_file x24, git commit in project tree, bash workdir bypass"]
        ALW["all ALLOWED by the pre-confinement allow-list: bash + filesystem + service"]
        BRD["deviation recorded on board wi-7382d739"]
        K --> DJ --> NT --> KA --> ALW --> BRD
    end

    %% OPERATIONAL ASYMMETRY (bottom, dashed border) - the brake
    subgraph BRAKE["OPERATIONAL ASYMMETRY - per-call env resolution vs boot-fixed os.environ"]
        direction LR
        O1["ALL switches in BALL lanes resolve per-call from os.environ"]
        O2["os.environ is fixed at boot - .env parsed only at boot by launcher.sh and run_app.py, no SIGHUP handler, no reload endpoint"]
        O3["turning a lane ON requires daemon restart: systemctl restart ensemble-main.service"]
        O4["live-acting brake: job_queues.is_paused on queue bd075f0b - instant OFF - is_paused=false since 2026-10-26"]
        O1 --> O2 --> O3
        O3 -.->|OFF needs no restart| O4
    end

    %% hard evidence facts at the edges
    EVA["HARD EVIDENCE - live keeper transcripts are SWEPT: 0 rows in instances, checkpoints, messages, tasks - incident evidence is documentary only"]
    EVB["METRICS DEFECT - capture_active is never passed at the API: F1/F3 report capture_off unconditionally, census reports dark/census_gated while publishing 15 live series - double inversion across metrics_census.py -> provider.py -> availability.py"]

    %% cross-region ties
    CRES -.-> O1
    E1 -.-> O1
    BRD -.- EVA
    CST -.- EVB

    %% styling
    classDef sw fill:#e8f0fe,stroke:#3367d6,color:#111111
    classDef gate fill:#fef7e0,stroke:#9a7b00,color:#111111
    classDef fact fill:#fce8e6,stroke:#c5221f,stroke-dasharray: 5 5,color:#111111
    class CSW,E1,E2,E3,E4 sw
    class CG1,CG2,CG3,EG1,EG2 gate
    class EVA,EVB fact
    style BRAKE fill:#f1f8f1,stroke:#2e7d32,stroke-dasharray: 6 4
    style INCIDENT fill:#fff5f5,stroke:#c5221f
```
