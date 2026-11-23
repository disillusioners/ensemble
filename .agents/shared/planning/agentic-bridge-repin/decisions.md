# agentic-bridge-repin — Decision Ledger

Lane: Coder (surgical incident commission, 2026-11-23). Commission: repoint the
agents that pin the dead llmproxy alias `agentic` onto a healthy model pool, as a
**BRIDGE**; the permanent fix is proxy-side and owned by the expert team.

## 1. Context (Ari's live diagnosis, 2026-11-23)

The llmproxy model id `agentic` returned HTTP 400
`{"error":{"type":"invalid_request_error","message":"openai: Invalid API parameter, please check the documentation."}}`
on every request on BOTH deployments (`llm.ensem.dev`, `llm.daoduc.org`). Healthy
pools at diagnosis time: `coding`, `coding2`, `quick`, `vision`, `agentic-go`,
`agentic-mini`, `agentic-turbo`. Consequence: every agent whose model was
`agentic` — directly, or via the `OPENAI_MODEL` default — died on its first model
call. The live install `.env` default was already repointed to `agentic-turbo`
(backup kept at `~/agents-ensemble/.env.bak-ari-outage-20261123T065555Z`) and
needs a daemon restart to load (restart is user-origin-gated; handled by Ari,
separate lane).

Four agent dirs still pinned `agentic` in their own `meta.json`, so the `.env`
change alone did NOT revive them:
`agents/aegis/meta.json`, `agents/sonic-developer/meta.json`,
`agents/sonic-leader/meta.json`, `agents/sonic-reviewer/meta.json`.

## 2. Decision ledger

| ID | Decision (one line) | Source | Rationale | Binding? |
|----|--------------------|--------|-----------|----------|
| D1 | Set `"llm_model": "agentic-turbo"` in exactly those FOUR `meta.json` files. Touch nothing else in those files. | Commission §1 | `agentic-turbo` is a healthy pool on both deployments (verified: see §3). The `.env` default change does not reach these four — the meta.json pin wins at priority-3 resolution. | ✅ |
| D2 | **This is a BRIDGE, not a fix.** The permanent fix is repairing the proxy's `agentic` alias (proxy-side; expert-team job `98c5b408` — died on the outage, to be re-dispatched). | Commission preamble | The alias, not the agents, is the defect. Repointing the agents is the smallest reversible change that revives them NOW. | ✅ |
| D3 | **REVERT (named so it is not forgotten):** once the proxy's `agentic` alias is healthy again AND the Emperor prefers the canonical alias, restore `"llm_model": "agentic"` in the same four `meta.json` files, restore the three sonic values in `test/packs/sonic_agent_defs_checks.py` (`EXPECTED_LLM_MODEL`: `sonic-leader` / `sonic-developer` / `sonic-reviewer`) and the three matching self-test fixture metas in the same file, and retitle the `FROZEN [sonic-llm-pins-v4]` block back to `v3`. `sonic-coder` / `sonic-tester` (`coding`) and the aegis children (`coding`) never moved and need no revert. | Commission §5 | A bridge without a named revert is a permanent fork. The revert surface is 6 values + one block title, all in the two files this lane touched. | ✅ |
| D4 | The single-model pin (`meta.llm_model`) is NOT gated by `allowed_models` at runtime — priority-3 resolution in `daemon/services/instance_lifecycle.py:2205-2214` slots it in verbatim; only the weighted `llm_models` pool is filtered (`_select_weighted_model`). Therefore `agentic-turbo` resolves even though it is not in the in-tree `_ALLOWED_MODELS_DEFAULT`. | Code reading (verified) | Governs whether the bridge needs a daemon-config change: it does not. Also corrects the sonic plan's R15 row (`.agents/shared/planning/sonic-team/plan-overview.md:112`), which conflates the weighted-pool path with the single-model pin. | ✅ |
| D5 | The daemon's shipped default selectable set (`_ALLOWED_MODELS_DEFAULT` + the `config.yaml` interpolation literal) is LEFT UNCHANGED. | Proportionality (commission §6) | The A4 pack leg's second loop is POOL VISIBILITY, not pin resolvability (D4). Changing the shipped default would touch `daemon/config.py`, `config.yaml`, `tests/unit/test_llm_allowed_models_precedence.py` (8+ literal assertions) and `docs/setup.md` — all outside the four-file scope — and would widen the revert surface (D3). The live deployment already supplies `agentic-turbo` via its explicit `OPENAI_SELECTABLE_MODELS`. | ✅ |
| D6 | No live promote. The release is staged only; the promote ceremony is the Emperor's. | Commission §4 | House rule: promote is a user ceremony. | ✅ |
| D7 | Release slot derived LIVE at bump time from the pushed tag set — never hardcoded, never a re-cut of a pushed tag. | Commission §4 | Siblings stack releases continuously; a hardcoded slot collides. | ✅ |

## 3. Independent probe of the premise (2026-11-23T15:03Z, this lane)

The commission's premise was re-tested against the proxy with the **live** key
(sha256 prefix `e33c34f5af4278a0` — byte-identical to the key in
`~/agents-ensemble/.env`), never printing the key itself:

| base URL | model | HTTP |
|---|---|---|
| `https://llm.ensem.dev/v1` | `agentic` | **200** |
| `https://llm.ensem.dev/v1` | `agentic-turbo` | 200 |
| `https://llm.daoduc.org/v1` | `agentic` | **200** |
| `https://llm.daoduc.org/v1` | `agentic-turbo` | 200 |

Request shapes probed: minimal (`max_tokens=8`), daemon-shaped (non-streaming +
`temperature` + `max_tokens=2000`), streaming (`stream=true`), and tool-bearing
(agent calls always send tools) — `agentic` returned 200 on every shape.

**So the `agentic` alias was ALREADY HEALTHY at probe time (2026-11-23T15:03Z)**
— roughly nine hours after the 06:00Z diagnosis. Either the proxy repaired it in
the interim, or the 400 required a request shape this lane did not reproduce.
This does NOT change the action taken (the repoint is cheap, reversible, and the
four pins were genuinely dead at diagnosis time), but it materially changes the
**priority of the revert (D3)**: the Emperor may prefer to revert to the
canonical `agentic` alias as soon as the expert team confirms the alias is
repaired, rather than carrying the bridge.

Recorded as an open item for the expert team (job `98c5b408`, re-dispatch):
confirm whether the `agentic` alias is repaired, and whether the original 400 is
reproducible — if it is not, the four pins may be reverted immediately.

## 4. Blast radius verified

| Surface | Result |
|---|---|
| `meta.json` pins on `agentic` | Exactly the four commissioned files — no others repo-wide (`grep -rln '"llm_model": "agentic"'`). |
| `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:119-140`) | `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `tools.md`, `skill.md`, `memory.md`, `growth.md` — **`meta.json` is NOT in the versioned set** (house note VERIFIED, not assumed). |
| Prompt-artifact baseline `agents/.prompt-artifacts.sha256` | 217 rows; `grep -c meta.json` = **0** — `meta.json` is not baselined, so **no baseline re-pin is required**. The generator's `--check` reports the same single pre-existing drift at base and head (`agents/maintenancer/workflow.md`, unrelated). |
| Seal map (`SEALED_SHA_BASELINES`, `tests/test_chart_image_delivery_e2e.py:1425`) | 35 rows; covers `agents/charter/*` only — **none of the four agents' `meta.json` is sealed**, so no seal re-pin is required. |
| Test pins asserting `llm_model == "agentic"` | Two: `tests/unit/agents/test_aegis_security_family.py:833` (aegis) and `test/packs/sonic_agent_defs_checks.py` `EXPECTED_LLM_MODEL` (leader/developer/reviewer). Both re-pinned in lockstep with the meta edits. |
| `tools_note` / docs mentions of the alias | None in the four agents' prompt artifacts. The `agentic` string elsewhere in `agents/`/`docs/` is either the `allowed_models` default list, the governor's `council_models`, or historical prose — **out of scope, unchanged**. |
| Out-of-scope pin found and REPORTED, not changed | `agents/governor/meta.json` `council_models: ["agentic", "coding"]` — a separate mechanism (deep-review council model list), not one of the four commissioned `llm_model` pins. Also `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` defaults to `"agentic"` (`daemon/config.py`). Flagged for the Emperor; not touched under the proportionality rule. |

## 5. Verification (base vs head — net-new delta)

All runs in an exclusive worktree off `origin/latest`; base probed with a
**scoped** `git stash push -- <6 explicit paths>` (never unscoped).

| Surface | Base | Head | Delta |
|---|---|---|---|
| `sonic_agent_defs` static layer | FAIL {A3, A8, A9} — 3 red | FAIL {A3, A8, A9} — same 3 red | **0 net-new** |
| `sonic_agent_defs` self-test | PASS (all A4 counter-mutation legs RED) | PASS (A4 legs still RED, non-vacuous) | 0 |
| `sonic_agent_defs` `--real` layer | PASS | PASS | 0 |
| `tests/unit/test_ari_pm_terminology_pins.py` | 23 passed | 23 passed | 0 |
| `tests/unit/agents/test_aegis_security_family.py` | 556 passed | 556 passed | 0 |
| meta.json-walking set (pok grants / anomaly grants / roster manifest / registry) | 5 failed, 135 passed | 5 failed, 135 passed (same 5) | **0 net-new** |

Pre-existing reds, unchanged and NOT caused by this lane: A3 (`sonic-leader`
allow array carries an extra `room` category), A8 (`agents/maintenancer/workflow.md`
prompt-baseline drift), A9 (steady-team fence — stale base `3f45cd6dd`, 33 residual
paths at base). **A9's residual grows by exactly one path
(`agents/aegis/meta.json`) — the leg was ALREADY red at base**, so no green leg
turned red. The A9 fence's own repair (its base is v0.30.4-era) is a separate
concern, owned by the tester lane.
