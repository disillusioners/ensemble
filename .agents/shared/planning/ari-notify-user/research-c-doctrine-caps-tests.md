# RESEARCH-C — Doctrine Census, Pin Set, Discord Caps, Test Conventions, Config Knobs, ari Prompt State

Commission: `ari-notify-user` (notify_user privileged ari-only tool). Worktree: `feature/ari-notify-user` @ d4ecdbd3d.
All paths repo-relative. Every claim carries file:line. Read-only investigation; this file is the only write.

---

## C1. DOCTRINE CENSUS — 'no ping path' and every close variant (BINDING)

Search patterns used (case-insensitive) across `daemon/ agents/ docs/ .agents/ config.yaml scripts/ test*/`:
`no ping path | ping path | no ping | never a ping | in silence | silent | no chat delivery | no delivery path | does not deliver | no push | no notification | no way to notify | cannot notify | board evidence | lands on the board | evidence lands | promise the board | user-visible surface | no chat identity | board-never-ping`.
Greps hit the 10k-file cap twice; each capped sweep was re-run narrowed per directory (daemon/, agents/, docs/, tests/, test/packs/, .agents/tester) — the sites below are the COMPLETE doctrine-teaching set found. `docs/` has ZERO doctrine sites (grep `self-alarm|self_alarm` over docs/ returned no matches). `agents/ari/soul.md`, `rule.md`, `user.md` do NOT teach this doctrine (their only related lines are the unrelated "Never Silently Accept Critical Decisions" rule — `agents/ari/rule.md:194-196`, `agents/ari/user.md:93`).

### [shipped-code] daemon/tools/self_alarm.py (docstrings ship in the binary via tool_help/args_schema/_full_doc_)

**D1. `daemon/tools/self_alarm.py:52-53`** — module docstring, obligation inventory:
> "The five §8.4 obligations (lateness in both cap states, board-never-ping,
> unobservable-fire, list semantics, the ``self-alarm:`` label namespace)
> SURVIVE unchanged."

Context: lines 50-54, the user-ruling-2026-10-20 retraction paragraph ("The user ruling of 2026-10-20 (Discord, direct) RETRACTS the earlier..." :50-51). The literal token `board-never-ping` names the doctrine as one of five binding obligations.

**D2. `daemon/tools/self_alarm.py:104-108`** — `CATEGORY_DOC` (category help text, shipped):
> "What the alarm is NOT: a notification. The scheduler source is one-way
> and the receiving instance has no chat identity, so there is NO chat /
> Discord / Slack ping and no interruption. The BOARD is the user-visible
> surface: the fired session acts (updates the board item, dispatches a
> job) and the board shows the result."

Context: `CATEGORY_NAME = "Self-Alarm"` at :90; `CATEGORY_DOC` block :91-112; the "Minimum horizon: 30 minutes" tail :110-111.

**D3. `daemon/tools/self_alarm.py:567-578`** — `self_alarm._full_doc_` (the per-tool long help; assigned at :559):
> "The board is the user-visible surface — there is NO ping"  (section heading, :567)
> "The promise: at the scheduled instant YOUR OWN session is woken with your
> message, performs the action and leaves evidence ON THE BOARD (status /
> notes) and in any job it dispatches; the live board view shows the change.
> What is NOT promised: a chat / Discord / Slack message, a reminder in the
> conversation, or an interruption. The scheduler source is one-way and the
> receiving instance has no chat identity — so the board is the only
> guaranteed surface."  (:569-575)

Context: preceded by the "fires into THIS session / message_to_self VERBATIM" paragraph :559-565; followed by the due-date/link_board_item convention :575-578.

**D4. `daemon/tools/self_alarm.py:529-532`** (doctrine-adjacent, args_schema) — `link_board_item` Field description: "when the alarm fires into THIS session you are told to board_get it before acting." The one-shot tool docstring at :537 defers to `_full_doc_` ("Use tool_help(\"self_alarm\") for details."), so D2/D3 are what the agent actually reads.

### [prompt-pin] agents/ari/** (protected sealed baseline — see C2 before editing)

**D5. `agents/ari/tools_note.md:758-764`** — Self-Alarm obligation 2 (THE canonical prompt statement):
> "2. **The board is the user-visible surface — promise the board, never a
>    ping.** At the scheduled instant THIS session is woken with my
>    message (same instance, context intact), performs the action, and
>    leaves evidence ON THE BOARD
>    (status/notes) and in any job it dispatches; the live board view shows
>    the change. I NEVER promise the user a chat/Discord/Slack ping, a
>    reminder in the conversation, or an interruption — no such path exists."

Context: the "Five obligations I keep straight every time I arm an alarm:" numbered list opens at :745; obligation 1 (lateness) :747-757; obligation 3 (unobservable fire) :765-769. The clause **"no such path exists"** (:764) is the exact sentence the new doctrine falsifies.

**D6. `agents/ari/tools_note.md:709`** — Board section cross-link:
> "The board is also where a fired self-alarm becomes visible — See Self-Alarm."

Context: closes the `### board_get` subsection (:704-707); doctrine-consistent, survives the amendment.

**D7. `agents/ari/workflow.md:320-325`** — "### Fired alarms surface on the board":
> "When a self-alarm fires, THIS session is woken with the alarm intent and
> performs the action, leaving its evidence ON THE BOARD (status/notes). The board — and the public board
> view — is where a fired alarm becomes visible to the user; I never promise
> a ping, a notification, or an interruption (See Self-Alarm)."

Context: sits between "### Timing — alarm vs sleep" (:314-318) and "### Patrol loop" (:327-338).

**D8. `agents/ari/workflow.md:334-335`** (doctrine-adjacent, patrol loop):
> "I note it on the board. I report to the user
> only if it matters to them."

Context: the alarm-wake patrol dispatches maintenancer with specifics (:329-334). Under the new doctrine "report to the user" gains a concrete mechanism (notify_user); passage should be AMENDED, not deleted.

### [docs] docs/**
No sites. Grep for `self-alarm|self_alarm` across `docs/` → no matches; the doc-side doctrine sweeps (`no ping|user-visible surface|promise the board`) matched only unrelated delivery prose (e.g. `docs/llm-stream-stall-hardening.md:82`, `docs/mcps-gateway.md:695`).

### [planning] .agents/shared/planning/** (historical plans — noted, lower priority)

**D9. `.agents/shared/planning/ari-tools/plan-overview.md:456`**:
> "4. **The board is THE user-visible surface of a fired alarm — no ping is promised.** When an alarm fires, the"

**D10. `.agents/shared/planning/ari-tools/decisions.md:481`**:
> "**R-chat-bridge** (no new infra; **the board is the user-visible surface**; no ping promised)"

**Excluded near-matches (NOT this doctrine):** `.agents/shared/planning/mcps-gateway-interop/plan.md:448` ("liveness ping silently does nothing" — WS heartbeat, different topic); `.agents/shared/planning/wedge-capture/phase2-plan.md:46` ("self-alarm" appears only as a naming precedent); `.agents/tester/MOCK_TESTS.md:211,235` + `.agents/tester/RESULTS/2026-08-23-p2-2-premerge-verification.md:51` ("spoofed/api-origin ... real stamping path" — origin-stamping refusal taxonomy).

### [test] tests asserting the doctrine

**D11. `tests/unit/test_self_alarm_registration.py:461-469`** — `test_obligation_2_the_board_is_the_surface_never_a_ping`:
> "The board is the user-visible surface — a ping is NOT promised."
asserts the flat doc contains: `"The board is the user-visible surface"` (:465), `"NO ping"` (:466), `"What is NOT promised"` (:467), `"Slack"` (:468), `"interruption"` (:469). Doc-flattening helper `_flat_doc()` at :441-444 pins PHRASES, not line breaks. Companion pins: `test_obligation_1...` :448-459, `test_obligation_3_the_fire_is_unobservable` :471-476, CATEGORY_DOC pin :188-191 ("self_alarm" in CATEGORY_DOC).
**Consequence for the doctrine-update phase:** editing D2/D3 wording REQUIRES updating these phrase assertions (the doc flattening tolerates re-wrapping, not re-wording).

**Census headline:** 3 doctrine-teaching sites in shipped code (D1-D3, +1 adjacent D4), 4 in sealed ari prompts (D5-D8), 0 in docs/, 2 in historical planning (D9-D10), 1 binding test pin (D11). Everything else matched by the sweep is off-topic delivery prose.

---

## C2. PROTECTED PIN SET + RE-STAMP CEREMONY

**Baseline definition & checker**
- Generator/verifier: `scripts/protection/generate_prompt_artifact_baseline.py` (166 lines). Regenerate: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (:19-20, "Regenerate the repo baseline (writes agents/.prompt-artifacts.sha256)"). Verify: `--check --tree <dir>` (:22-23), read-only, "Exit codes (--check): 0 = clean or baseline absent · 1 = drift found" (:25). Output is deterministic: "sorted paths, \"<sha256>  agents/<dir>/<artifact>\" lines (sha256sum -c compatible ...)" (:26-27).
- Single source of the artifact SET: `daemon/tools/prompt_artifacts.py:119-137` — `VERSIONED_PROMPT_ARTIFACTS: frozenset[str] = frozenset({...})` covering `soul.md, rule.md, workflow.md, tools_note.md, tools.md` (:120-124) + `memory.md`, `growth.md` (:127-136); loaded BY FILE PATH by the generator to avoid the langchain import (:40-58).
- Baseline file: `agents/.prompt-artifacts.sha256`. **CURRENT COUNT VERIFIED IN THIS WORKTREE: exactly 161 rows** (file is 161 lines; each line `<sha256>  agents/<dir>/<file>`). The agents/ari rows are lines 26-29: `agents/ari/rule.md` (:26), `agents/ari/soul.md` (:27), `agents/ari/tools_note.md` (:28), `agents/ari/workflow.md` (:29) — **all four ari prompt artifacts are sealed with exact content hashes** (their `tools.md` legacy fallback and no memory/growth rows exist for ari, hence 4 not 7).
- Enforcement legs: stage-time via `scripts/upgrade/stage.sh` (generator docstring :9-12 "drift is DETECTED ... at stage time (scripts/upgrade/stage.sh, read-only + journaled, never gating per the allow-stale policy)"); boot WARN via `log_baseline_drift_at_boot` in api.py lifespan (prompt_artifacts.py:586 "[Startup] Prompt-artifact baseline clean: %d versioned ..."); test leg `tests/test_prompt_artifact_baseline.py` (CLI exit-code contract :11-13; `sha256sum -c` compat :107-119; drift classes modified/missing/added/malformed via `pa.parse_baseline` + `pa.check_baseline_drift`, generator :127-144).

**Exact-set tripwire (the 35-file SEALED_SHA_BASELINES)**
- `tests/test_chart_image_delivery_e2e.py:1412-1476` — `SEALED_SHA_BASELINES: dict[str, str]` = **35 exact-set content-sha256 pins**; membership is fixed by the class docstring :1488-1493 ("Phase A (8 files ...), Phase B (7 daemon files), Phase C (20 agent canonical-homes ...). Total 35."). Check loop :1496-1528 fails on BOTH `missing` and `mismatches` (exact-set semantics, not count).
- ari files in the exact-set: **`agents/ari/workflow.md`** (:1421, sha `fd481d9473...`) and **`agents/ari/rule.md`** (:1452, sha `bb784e8c41...`). ari `tools_note.md`/`soul.md` are NOT in the 35-file tripwire (they are covered only by the 161-row manifest).
- Summary of "exact-set vs count-asserted": the 35-file tripwire = exact-set (missing file or changed byte both fail); the 161-row manifest = per-row exact hashes with added/missing row classification (functionally exact for rows; the "161/161" figure is the verified_count reported by the checker, cf. prompt_artifacts.py:448 "PROMPT-BASELINE: OK — {self.verified_count} versioned ...").

**Re-stamp / re-pin procedure (as the repo practices it)**
1. Edit the prompt artifact(s) (e.g. `agents/ari/tools_note.md`, `agents/ari/workflow.md`).
2. Regenerate the manifest: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (rewrites `agents/.prompt-artifacts.sha256`; deterministic, so only the edited rows change).
3. For every edited file that is in `SEALED_SHA_BASELINES`, recompute its content-sha256 and update that ONE dict row IN THE SAME COMMIT, with an attribution comment. Precedents documented in-file:
   - 2026-10-20 ari re-pin: `tests/test_chart_image_delivery_e2e.py:1402-1411` — "the prompt-artifacts manifest row was re-stamped in the SAME commit (161/161 OK). The tripwire row had to be re-pinned here or the binding 0-stale gate would go red — this is the adjudicated deviation ... Only this ONE row changed; the other 34 keep their values. The value is a CONTENT-SHA256 of the sealed file (NOT a commit sha)".
   - `daemon/sources/registry.py` re-pinned 2026-10-18 (:1424-1429); `daemon/constants.py` re-pinned 2026-10-21 (:1438-1447); `agents/maintenancer/rule.md` re-pinned 2026-10-20 with the pre-paragraph baseline value (:1460-1467).
4. Evidence a re-pin commit carries (house pattern, from the comments above): the recomputed content-sha256 inline next to the row + a dated attribution comment naming the ruling/commission and stating "Only this ONE row changed; the other N keep their values" + the regenerated manifest row in the same commit (the "161/161 OK" claim).
- Note: `git log` grep was NOT executable here (read-only agent, no bash); the re-pin history above is quoted from the in-file provenance comments, which is where the repo records it.
- Test-consumer caveats already in-tree: `tests/test_chart_image_delivery_e2e.py:1293,1323` record that dispatcher-side edits did NOT require a manifest regen because "dispatcher.py is not an [artifact]" — only depth-2 `agents/<dir>/<artifact>` files count (prompt_artifacts.py:248-250, :355-369).

---

## C3. DISCORD ADAPTER CAPS

- **2000-char constant:** `daemon/sources/adapters/discord/constants.py:34-35` — `# \`DiscordAdapter._split_message()\`.` + `DISCORD_MAX_MESSAGE_LENGTH = 2000`.
- **Split algorithm:** `daemon/sources/adapters/discord/adapter.py:1280-1344` `_split_message(content, max_length=DISCORD_MAX_MESSAGE_LENGTH)`; docstring :1285-1291 "5-tier priority boundary chain (FR-22): 1. Paragraph (`\n\n`) 2. Line (`\n`) 3. Sentence (`. `/`! `/`? `) 4. Word (space) 5. Hard cut at max_length"; returns `[content]` unchanged if it fits (:1296-1299); recursive per-oversized-chunk application (:1293-1294); hard-cut defensive loop guard :1330-1339.
- **Where splitting happens in the send path:** `adapter.py:1761` `chunks = self._split_message(content)`; the loop sends each chunk sequentially under the per-channel lock + send semaphore (:1762-1765, `async with lock: async with self._send_semaphore:`); attachments ride chunk 1 only (:1770-1778, "Chunk 1 (atomic first unit — amendment #6)... Chunks 2..N: text-only").
- **Per-channel (per-user) locks:** `adapter.py:268-271` `_channel_locks: OrderedDict[str, asyncio.Lock]` + guard; `_get_channel_lock` :789-824 with LRU `move_to_end` (:802-804) and eviction of unheld locks up to `MAX_CHANNEL_LOCKS` (imported :69; doc header :28 "per-channel ``OrderedDict`` LRU lock (capped at 1000)"; degenerate all-held case :816-819). Doc header :25-28: "splitting content longer than 2000 chars at [boundaries] ... Wraps sends with circuit breaker (5 failures → open for 60s)".
- **Circuit breaker:** `adapter.py:261-262` `self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)` (class from `daemon/sources/circuit_breaker.py`, imported :57); reset on adapter start :382.
- **Concurrency semaphore + rate-limit metric:** `daemon/sources/adapters/discord/resilience.py:12-13` `DiscordSendSemaphore` ("not DiscordRateLimiter"); `rate_limit_waits` counter :70-72, :92, :119 (discord.py handles per-route buckets via `X-RateLimit-*` headers, :5).
- **Token-bucket rate limiter (general sources layer):** `daemon/sources/rate_limiter.py:25-74` `TokenBucketLimiter` (refill :48-53, acquire :38-58, `wait_and_acquire(max_wait=5.0)` :60-74); `RateLimit` dataclass :9-13; `DEFAULT_RATE_LIMITS` :17-22 covers telegram/webhook/whatsapp/slack — **no discord entry** (discord relies on discord.py's own buckets + the semaphore).
- **What a TOOL-LEVEL cap should be:** the adapter ALWAYS splits a >2000-char payload (:1761 + :1298) into N boundary-aware messages sent back-to-back under the channel lock. So a notify_user payload >2000 chars would NOT fail — it would arrive as multiple chat messages. For a deliberate user notification that is undesirable (one alarm → message burst; multi-message spam reads as breakage and can interleave with other traffic under the shared channel lock). The tool should therefore cap/trim content at ≤2000 chars itself (or explicitly pre-split with "(.../N)" continuation) so the contract "one notify = one chat message" holds; `DISCORD_MAX_MESSAGE_LENGTH` (`constants.py:35`) is the natural bound to reuse.

---

## C4. TEST CONVENTIONS

**(i) Unit test pack layout.** pytest packs live in `tests/unit/…` (e.g. `tests/unit/test_self_alarm_registration.py`); each commission ships a wrapper pack script in `test/packs/<name>_test.sh`. House pack-script header (read: `test/packs/self_heal_zero_row_unit_test.sh:1-41`): title line "Test Pack: <name> — <what>"; prose of the fix under test; "Files covered:" with per-file test counts (:24-28); "Branch-under-test:" (:30-31); dual-layer timeout — "Layer 1 (command-level): caller wraps with `timeout 300`" + "Layer 2 (script-internal): `timeout 120s` on the pytest process" (:33-35); exit codes "0 PASS / 1 FAIL / 124 TIMEOUT" (:37-40); `set -euo pipefail` + HEAD echo (:41-48) and `set +e` around the timed command so the FAIL branch is reachable (:52-55). The verdict ledger is `.agents/tester/PACKS.md` (rows like `| <pack> | <commit> | <type> | <date> | ✅ verdict |`, e.g. :124, :139; per-commission VERDICT header blocks e.g. :4, :14).

**(ii) Mutation-verified pin convention (planted-defect → RED → restore → GREEN).** Canonical example: `tests/test_adopt_unit.sh` — leg structure documented at :30 ("THROWAWAY template copy under `$WORK` → zero-env pins RED → restore"), :477-479 (`_pass "mutation (RED): planted minted line DETECTED by the zero-env pin"`; the failure mode is named `"pin stayed GREEN (mutation-dead)"`), :482 ("leg 3 (restore → GREEN): restore the pristine template copy"), restated at :577, :602, :618. Same pattern: `tests/integration/test_fresh_episode_p0_facade_guards.py:43` ("mangle-matrix self-proof (mangle → RED → restore → GREEN"). House rule: a pin must be proven RED against a planted defect on a THROWAWAY copy (repo files never mutated — :577 "Repo files never mutated"), then GREEN after restore; a pin that stays green is "mutation-dead" and the pack FAILS.

**(iii) Registration pin example.** `tests/unit/test_instance_ops_registration.py` (550 lines): module docstring IS the pin matrix — (a) `CATEGORY_MODULES` carries `daemon.tools.*` paths only (:7-10), (b) categories ∈ `PRIVILEGED_TOOL_CATEGORIES` (:11-12), (c) "the frozenset LENGTH is 14" (:13 + expected set :79-90 "the 12 pre-existing entries plus the two nest-ops categories (12 -> 14, D-13)"), (d) tool names ∈ `DYNAMIC_TOOL_NAMES`/`KNOWN_TOOL_NAMES` (:13-14), (e) AST-scanner finds every `@tool` at any depth — "decorator-only registration is SILENTLY INVISIBLE" (:15-16), (f) extends-before-help ordering (:17-20), (g)/(h) source-level wiring + vocabulary pins (:21-30). Symbols imported from `daemon.tools._tool_registry` (:48-54); expected tool-name frozensets declared at top (:63-77). This is the template a `notify_user` registration pin should follow (category in `PRIVILEGED_TOOL_CATEGORIES`, allow-list ari-only, AST registration, privileged-universe length bump).

**(iv) Isolated boot smoke + mock-LLM conventions.**
- cgroup-isolated boot smoke examples: `.agents/tester/PACKS.md:9` (`v0276_boot_smoke` — "staged binary COPIED + sha-verified into mktemp FAKE_HOME layout; `systemd-run --user` zero-env transient; throwaway PG16 role+DB; per-var scrub; live state.json sha watch; live fence ×4 ... banner/livez/readyz/version, cgroup isolation, state.json no-change, live untouched, cleanup"), :22 (`unit_secret_boot_smoke` — "scratch install layout (FAKE_HOME + mktemp + escape guard ...); throwaway PG16 role+DB; per-var scrub; `systemd-run --user` zero-env transient; scratch port 10000-19999"), :124 and :139 (`v0263_staged_boot_smoke` / `v0262re_gb_bootsmoke` — "systemd-run transient unit cgroup-proven; banner ×2; livez/readyz 200; 3 board tables; baseline 161; ... live fence intact"; NOTE at :124 "BOTH `PORT`+`DAEMON_PORT` needed (staged config.yaml falls back `${PORT:-8088}`)").
- Harness doctrine: `.agents/tester/LESSONS/2026-10-21-hard-reboot-p4-drill-harness.md:30-45` — L2: "Tool/bash children INHERIT THE LIVE DAEMON'S CGROUP (`/proc/<pid>/cgroup` of a tool child = `/system.slice/ensemble-main.service`) ... isolation via `systemd-run --unit=<n> --wait --collect ...` → `/system.slice/<n>.service`, and isolation must be **ASSERTED** by reading `/proc/<pid>/cgroup` before any signal — never assumed"; L3: "`systemd-run` needs no sudo; `systemctl stop` does" (:43-45). Also `.agents/tester/LESSONS/2026-10-21-v0262-ruling-e2e-lessons.md:23` — "`systemd-run --user` transient unit (needs explicit `XDG_RUNTIME_DIR=/run/user/1000` in agent shells) gives cgroup-level proof of isolation for ~zero cost — quote `/proc/<pid>/cgroup` in evidence". E2e precedent with scratch PG + DROP-verified teardown: `.agents/tester/PACKS.md:57`.
- Mock-LLM methodology: `.agents/tester/MOCK_TESTS.md` is the inventory ("Update when mock tests are added/modified" :18-20); each mock entry documents Metadata/Configuration/What It Tests/Mock Services Required/Test Scenarios (:24-70; example: real repos + `MagicMock`/`AsyncMock` absorbing the LLM/checkpointer surface :47-52). Mocks live under `tests/mocks/*.py` (:26). E2E lane: fake chat source server `tests/e2e/mock_source_server.py` (:43 imports; :128 "external_user_id: Stable per-user id (used for instance mapping)") with per-test module swapping (`tests/conftest.py:300` "swapping the mock for the real module per-test (see tests/e2e/conftest.py)"; `tests/e2e/conftest.py:17,175`). Pack-level scripted-LLM helper: `test/packs/lcan_helpers/mock_llm.py`. In-repo chat-harness seam for driving turns without a real LLM: `tests/integration/chat_source_harness.py` (:60, :225-228 chat-lane flipping). (No single test file was found that scripts an LLM emitting a named tool call; the house mechanism is the conftest module-swap + mock servers above — a notify_user emission test would follow that seam.)

---

## C5. CONFIG KNOB PATTERN (for NOTIFY_USER_* knobs)

- Declared in `daemon/config.py` as a `BaseSettings` section class with `SettingsConfigDict(env_prefix=...)`. Closest neighbor — `SchedulingConfig` (`daemon/config.py:2716-2784`): `model_config = SettingsConfigDict(env_prefix="ENSEMBLE_SCHEDULING_")` (:2730); docstring notes tests auto-track the prefix via `tests/conftest.py::_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` (:2724-2727, "no conftest edits required for new keys under this prefix").
- Field style examples (each = `Field(default=…, description="… Env: <DERIVED_ENV_NAME>.")`):
  - `one_shot_max_lateness_seconds: int | None = Field(default=None, description="Max lateness (seconds) ... None = unlimited ... Beyond cap = no dispatch, write SKIPPED schedule_executions row. Env: ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS.")` (:2738-2744) — the self-alarm lateness knob the tools echo as `lateness_cap_seconds` (`daemon/tools/self_alarm.py:582-584`).
  - `default_timezone: str | None = Field(default=None, description="Default IANA timezone ... Env (auto-derived from field name): ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE.")` (:2732-2737).
  - Bounds-checked ints use `ge=`: `defer_max_count: int = Field(default=16, ge=1, ...)` (:2766-2774) and `defer_max_age_seconds: int = Field(default=86400, ge=60, ...)` (:2775-2784) — DEC-19 deferred-delivery bounds, fire-tick-only checks (:2761-2765).
- `config.yaml` mirrors the class field names EXACTLY in a named section with the env-override note (`config.yaml:514-529`: "Field names mirror daemon/config.py:SchedulingConfig exactly. Env vars override these via the ENSEMBLE_SCHEDULING_* prefix (auto-covered by tests/conftest.py::_TRACKED_ENV_PREFIXES...)"; `one_shot_max_lateness_seconds: null` at :523). The `'${VAR:-default}'` interpolation style is used elsewhere in config.yaml, e.g. `model: ${OPENAI_MODEL:-gpt-4}` (:20) and the services watchdog knobs (:506-508).
- Prescription for notify_user: a `NotifyUserConfig(BaseSettings)` with `env_prefix="ENSEMBLE_NOTIFY_USER_"` and `per_turn` / `per_hour` / `timeout_seconds` / `retries` int Fields (with `ge=` bounds and `Env:` lines in the descriptions), plus a `notify_user:` block in config.yaml mirroring the field names (null-able defaults like `one_shot_max_lateness_seconds: null`).

---

## C6. AGENTS/ARI CONVENTIONS — CURRENT STATE (passages the new wording replaces/amends)

**Passages to REPLACE/AMEND** (full quotes in C1):
- `agents/ari/tools_note.md:758-764` — obligation 2, ending **"— no such path exists."** (:764). Amend: evidence lands on the board ALWAYS; notify_user MAY additionally voice to chat when the content needs the user.
- `agents/ari/workflow.md:320-325` — "Fired alarms surface on the board" + "I never promise a ping, a notification, or an interruption" (:324-325).
- Adjacent: `agents/ari/workflow.md:334-335` "I note it on the board. I report to the user only if it matters to them." (gains the notify_user mechanism); `agents/ari/tools_note.md:709` board↔alarm cross-link (survives).
- Shipped-code counterparts to keep in sync: `daemon/tools/self_alarm.py:104-108` (CATEGORY_DOC "What the alarm is NOT: a notification...") and `:567-575` (`_full_doc_` "there is NO ping / ... the board is the only guaranteed surface") — plus the phrase-assertions they feed at `tests/unit/test_self_alarm_registration.py:461-469` (D11).

**Current tools documented in tools_note.md** (for the new tool section's placement/format): Board tools `board_create/board_update/board_list/board_get` (`## Board — Durable Cross-Instance Work Items`, :638-707), `self_alarm` (`## Self-Alarm`, :713-794), `view_write` + `board_link` (`## Live Views — Published Pages & the Public Board`, :798-837); also image generation with marker discipline (:629-632). meta.json category allow entries for these: `agents/ari/meta.json:39-42` (`"board"`, `"self-alarm"`, `"board-link"` — plus `"view-views"` family nearby).

**Doc format used (match it):** `##` tool-family heading; 1-2 sentence purpose; fenced ```raw call example with inline `#` arg comments (tools_note.md:726-734); response-fields paragraph (:736-743); bold numbered "obligations" list with bold lead phrases (:745-778); bold convention paragraphs ("**Re-arming (label reuse):**" :780, due_at convention :791-794); `###` per-tool subsections in the Board section (:668-707); See-X links ("See Self-Alarm" :709). workflow.md uses `###` habit subsections under `## Session Open & Close — the Board Habit` (:282-338).

---

## Cross-cutting facts for the planner
- The wake seam notify_user would reuse is documented at `daemon/tools/self_alarm.py:37-48` (reuse_instance delivery: `enqueue_message_job` → `_process_message_with_tracking` → role="user" HumanMessage) and :124-134 (`SELF_ALARM_INSTANCE_MODE = "reuse_instance"`); authorized-agent pattern: `SELF_ALARM_AUTHORIZED_AGENT = "ari"` (:114-116) with the three-layer posture (:19-29) — the same shape board_link/instance/mcp_rpc use (`daemon/tools/board_link.py:31`, `daemon/tools/instance.py:2397,2434`, `daemon/tools/view_write.py:24`).
- Bound chat-source routing precedent: `agents/ari` tools that hand the user content today are view_write/board_link (public links), not chat sends — no existing ari tool sends chat prose; that is exactly the gap notify_user fills.
