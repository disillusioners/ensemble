# RESULTS — tool-pairing original-symptom closure V2 (invalid_tool_calls shape)

- **Date**: 2026-10-09
- **Pack**: `test/packs/tool_pairing_original_symptom_v2_mock_test.sh` (dual-layer 240 s / `timeout 300`)
- **Test file**: `tests/integration/test_tool_pairing_original_symptom_v2.py` (NEW, 643 lines)
- **Worktree**: `/home/nea/ensemble-src-wt-pairing-heal-2` (branch `fix/tool-pairing-invalid-tool-calls`, HEAD `375aed46a`, `.venv` 3.14.7)
- **Commission gate**: round-2 item 2 — THE round-2 closure criterion (task 10816, incident 03d7657f round 2)
- **Spec**: `.agents/tester/MOCK_TESTS.md` § "tool-pairing original-symptom closure V2 — invalid_tool_calls shape (round-2 criterion, task 10816)"

## RESULT: PASS

- v2 pack: **4 passed in 1.50 s** (pytest), pack exit 0, `RESULT: PASS`
- v1 regression pack re-run: **4 passed in 1.24 s**, `RESULT: PASS` (v1 file untouched)

## Per-arc evidence

| Arc | Assertions | Evidence |
|---|---|---|
| (a) | union gateway rejects raw `invalid_tool_calls`-only poison; v1-view negative control ACCEPTS (blind spot); round-1-strip simulation rejected again; UNREACHABLE via real W1 | `_reject_directly` raised canonical `(2013)` BadRequestError on raw poison (id in body); `_V1StrictGatewayLLM.invoke(raw)` returned OK with zero rejections (proves the union is the load-bearing delta); strip of the uuid TM re-exposed the unanswered id → `(2013)` again; node run: 1 invoke, `_unanswered_invalid_ids(payload) == []` for every captured payload |
| (b) | W1 synth for the invalid call carries the INVALID flavor | payload's synth TM: `tool_call_id == X_B`, `id == partner-synth-{X_B}`, `content == PARTNER_SYNTH_INVALID_TEXT` and `!= PARTNER_SYNTH_TEXT`; `has_pairing_violations(payload) is False`; 1 invoke; node OK |
| (c) | W2 heal-once + retry, identity survival | exactly 2 invokes, 1 rejection; RAW raised BadRequestError asserted `(2013)`-shaped (unioned signature) via `CapturingGateway.raised_raw`; synth TM present in BOTH payloads — same id string `partner-synth-{X_C}`, same Python object (`synth1 is synth2`), adjacent block in retry (`payload2[ai_idx+1] is synth2`); uuid-id TM (`1c2a9d4f-…`) retained by identity in both payloads; retry accepted; node OK |
| (d) | verbatim live tuple: probe CLEAN, zero removal | `call_8ed9e1771dca42348dfa7ca0` + TM id `1c2a9d4f-3b71-4f0e-9a23-deadbeef0001` mid-list in 602 msgs: `has_pairing_violations is False`; 1 invoke, no rejections; `any(m is live_tm for m in payload)` and `any(m is live_ai for m in payload)` — identity preserved; adjacency intact (`payload[ai_idx+1] is live_tm`) |

## Gateway delta (the ONLY harness change)

`UnionStrictGatewayLLM(_V1StrictGatewayLLM)` overrides `_find_violation` with one changed line: the needed-set comes from the PRODUCTION `daemon.tool_pairing_history._extract_tool_call_ids` (union of `tool_calls` + `invalid_tool_calls`, per the two-tier evidence basis documented at `daemon/tool_pairing_history.py:230-247`). Walk logic, sentinel exemption, and the `_make_pairing_invalid_bad_request` body builder are reused from v1 unchanged. The v1 file was imported, never modified.

## Deviations (documented, none silent)

1. **Arc (c) "synthesized TM from the W2 heal"**: W1 and W2 run the SAME idempotent `_ensure_full_history_pairing` helper on the same in-place list — a W2-ONLY-minted synth is unreachable by construction (anything healable was healed pre-dispatch; graph's own W2 defensive comment covers the no-heal single-retry branch). The test pins the same CONTRACT: the synth survives the W2 cycle into the retry payload by identity (deterministic `partner-synth-{tc_id}` id makes the id-string assertion hold regardless of which pass minted). Documented in the test docstring.
2. **Arc (c) 2013-shape assertion target**: the v1 `rejections` list stores only the walk reason (the fail-once fiction reason carries no literal `(2013)`); the canonical body prefix rides the RAISED exception message. Quick-fix (<20 lines, test-only): `CapturingGateway` captures each raw `BadRequestError` and the assertion targets `raised_raw[0]`.

## Cleanup

Pure in-process — no ports, no daemon, no processes to leak. `git status` after run: only the four sanctioned paths dirty (v2 test, v2 pack, MOCK_TESTS.md, PACKS.md).
