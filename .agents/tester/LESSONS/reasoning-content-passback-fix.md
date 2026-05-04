# Reasoning Content Passback Fix — Testing Lessons

## Fix Overview
`ThinkingChatOpenAI._get_request_payload()` in `daemon/graph.py` overrides to re-inject `reasoning_content` from AIMessages into API request payloads, because LangChain's `_convert_message_to_dict()` strips it.

## Key Testing Insights

### Index-based pairing
The fix uses `assistant_idx` counter to pair payload assistant messages with original AIMessages. This is ordering-dependent but reliable since `_convert_message_to_dict` preserves message order.

### Empty string handling
Critical: `if reasoning is not None:` is used (not `if reasoning:`), so empty strings `reasoning_content: ""` are correctly preserved.

### Known gap: `reasoning` alternate key
- `_generate` override checks both `reasoning_content` and `reasoning` keys
- `_get_request_payload` only checks `reasoning_content`
- If a provider puts thinking under `reasoning` key, it won't be re-injected
- Low risk: DeepSeek and OpenAI o-series both use `reasoning_content`
- Documented in `test_additional_kwargs_reasoning_key_not_injected`

### Test patterns
- Mock `super()._get_request_payload()` to return stripped payloads
- Then verify `_get_request_payload()` correctly re-injects reasoning
- Use `additional_kwargs={"reasoning_content": "..."}` on AIMessage
