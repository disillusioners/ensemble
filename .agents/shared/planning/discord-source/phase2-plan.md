# Phase 2: Message Flow

## Objective

Implement bidirectional message normalization: inbound Discord `on_message` events → ensemble `IncomingMessage` dataclass, and outbound `OutgoingMessage` → Discord channel/thread/DM send. This phase implements the core chat loop — users can message the bot and receive agent responses.

## Files to Create

| # | File | Purpose |
|---|------|---------|
| 1 | `daemon/sources/adapters/discord/formatting.py` (fill stub) | `_strip_llm_artifact_tags()`, `_clean_discord_text()` |

## Files to Modify

| # | File | Change |
|---|------|--------|
| 1 | `daemon/sources/adapters/discord/adapter.py` | Add `on_message` event handler, `_normalize_incoming()`, `_build_external_user_id()`, `_is_bot_mentioned()`, `_should_process_message()`, `send()`, `_route_outgoing()` |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Implement `_strip_llm_artifact_tags(content)` in `formatting.py`: copy the regex pattern from `telegram.py:37-71` verbatim — strips `<think>`, `<reasoning>`, `<scratchpad>`, `<reflection>` in block, self-closing, and orphan forms. This is reused for Discord because Discord renders markdown natively and these tags would display as raw text. | none | Input `<think>secret</think>hello` → output `hello`; all 4 tag variants stripped |
| 2 | Implement `_clean_discord_text(text)` in `formatting.py`: strip Discord mention tokens (`<@user_id>`, `<@!user_id>` (nickname mention), `<@&role_id>`, `<#channel_id>`); strip `<!here>`, `<!everyone>` equivalents; collapse whitespace. Pattern: `_clean_message_text` in `slack/adapter.py:702-752`. | none | Input `<@123456789> hello <#987654>` → `hello`; mentions stripped, content preserved |
| 3 | Implement `_is_bot_mentioned(message)` in `adapter.py`: check DM channel (always True — no mention needed); check guild message for `<@{bot_user_id}>` or `<@!{bot_user_id}>` in `message.content`; check `message.mentions` list for bot user. Pattern: `_is_bot_mentioned` in `slack/adapter.py:666-700`. | Phase 1 Task 3 (bot_user_id captured) | DM → True; guild @mention → True; guild no-mention → False; bot ID not resolved → fail open (True) |
| 4 | Implement `_build_external_user_id(message)` in `adapter.py`: DM → `dm:{user_id}`; guild channel → `{guild_id}:{channel_id}`; guild thread → `{guild_id}:{parent_channel_id}:{thread_id}`. Pattern: `_build_external_user_id` in `slack/adapter.py:835-872`. | none | DM message → `dm:123456789012345678`; channel message → `987654321098765432:555444333222111333`; thread message → `987654321098765432:555444333222111333:777888999000111222` |
| 5 | Implement `_should_process_message(message) -> bool` in `adapter.py` as the **earliest gate** in the message pipeline (runs before mention check and normalization). Returns True if the message should be processed, False if it should be skipped. Implements three filters in order: (a) **Guild filter** — if `self._allowed_guilds` is set and non-empty AND `message.guild.id` (as int) is not in the list → return False (log at debug). (b) **Channel filter** — if `self._allowed_channels` is set and non-empty AND `message.channel.id` (as int) is not in the list → return False (log at debug). For threads, the parent channel id is checked against `allowed_channels` (use `message.channel.parent_id` when present). (c) **Cross-bot allowlist** — by default skip ALL messages where `message.author.bot == True`; if `self._allowed_bot_ids` is set and non-empty, allow messages whose `message.author.id` is in the list (override the default skip). If both `allowed_guilds` and `allowed_channels` are empty/unset, all guilds/channels pass (default allow). DMs always pass the guild/channel filter (no guild context). | Phase 1 Task 3 (`allowed_guilds`, `allowed_channels`, `allowed_bot_ids` captured from config) | message from disallowed guild → not processed (False returned); message from allowed guild + disallowed channel → not processed; message from allowed guild + allowed channel + bot author not in `allowed_bot_ids` → not processed; message from allowed guild + allowed channel + human author → processed (True); all filters unset/empty → all messages pass (default allow); DM with no guild → passes guild/channel filter |
| 6 | Implement `on_message` event handler (discord.py `@client.event`): filter own messages; call `_should_process_message()` (Task 5) FIRST → skip if False (debug log); check `_channel_require_mention` → skip if not mentioned (guild only); call `_normalize_incoming()`; emit via `_emit_message()`. Register in `start()` (Phase 1). Pattern: Slack `_handle_message_event` in `slack/adapter.py:618-664`. | 3, 4, 5 | Bot ignores own messages; message gated by `_should_process_message()` (guild/channel/cross-bot allowlist) is skipped with debug log; guild no-mention messages skipped with debug log; DM and mentioned messages emit `IncomingMessage` |
| 7 | Implement `_normalize_incoming(message)` → `IncomingMessage`: extract content (`message.content`); **handle attachments (FR-17)** — iterate `message.attachments`; for each attachment collect `attachment.url` into `IncomingMessage.images` as a `list[str]` of URLs (in attachment order); if `message.content` is empty/None but `len(message.attachments) == 1`, set `content = "[Image attachment: {attachment.filename}]"`; if `message.content` is empty/None and `len(message.attachments) > 1`, set `content = "[{n} image attachment(s)]"` where `{n}` is the count; if both content and attachments exist, keep BOTH (content populated AND `images` list populated — do not overwrite content with the placeholder); clean text via `_clean_discord_text()`; detect commands (`/new`); **extract reply_to_id (FR-20, inbound side)** — if `message.message_reference` is set and `message.message_reference.message_id` is not None, populate `IncomingMessage.reply_to_id` with `str(message.message_reference.message_id)`; build nested metadata dict (`{"discord": {...}, "agent": self._default_agent}`); set `external_user_id` via `_build_external_user_id()`. Pattern: `_process_event` in `slack/adapter.py:754-833`, `_process_update` in `telegram.py:491-601`. | 1, 2, 4 | Returns `IncomingMessage` with correct canonical external_user_id, cleaned content, and nested discord metadata + agent key; message with one or more attachments → `IncomingMessage.images` populated with URLs in attachment order; message with only an attachment (no text content) → `content` set to descriptive placeholder (`"[Image attachment: foo.png]"` or `"[3 image attachment(s)]"`), NOT dropped as empty; message with both text and attachments → both content and images populated; reply message → `IncomingMessage.reply_to_id` set to referenced message ID (str); non-reply → `reply_to_id` left as None |
| 8 | Implement `reply_to_id` mapping (FR-20, outbound side) in `send()`: if `OutgoingMessage.reply_to_id` is set (non-None), map it to discord.py's `MessageReference` and pass it as the `reference=` kwarg (Discord semantic: `message_reference`) on each chunk send so the bot's reply is threaded as a Discord reply. Use `discord.MessageReference(message_id=int(reply_to_id), fail_if_not_exists=False)` so the reply still posts as a normal message when the referenced message has been deleted (no raise). When `OutgoingMessage.reply_to_id` is None, do NOT pass the `reference` kwarg. | 7 | `OutgoingMessage.reply_to_id` set → Discord `reference=MessageReference(...)` passed on every chunk send; `OutgoingMessage.reply_to_id` None → `reference` kwarg omitted; reply to a deleted message → still posts (no exception), falls back to non-threaded send |
| 9 | Implement `_split_message(content, max_length=2000)` and use it in `send(message: OutgoingMessage) -> bool`: signature `_split_message(self, content: str, max_length: int = 2000) -> list[str]`; if `len(content) <= max_length`, return `[content]`; otherwise apply a **priority-ordered fallback chain of 5 split tiers** per chunk that exceeds `max_length`: (1) **Paragraph boundary** (`\n\n`) — split at the nearest double-newline before the `max_length` limit; (2) **Line boundary** (`\n`) — if no paragraph break fits within the limit, split at the nearest single newline; (3) **Sentence boundary** (`. `, `! `, `? `) — if no line break fits, split at the nearest sentence-ending punctuation followed by a space (search backwards from `max_length`); (4) **Word boundary** (space) — if no sentence boundary fits, split at the nearest space character; (5) **Hard cut** — if no space fits within the limit (e.g., a single very long word/URL/identifier), cut at `max_length` exactly. For each chunk that exceeds `max_length`, the algorithm tries tier 1, then 2, then 3, then 4, then 5 in strict priority order; whichever tier yields a valid split point ≤ `max_length` is used. Each resulting chunk must be ≤ `max_length`. The fallback chain is applied to each oversized chunk independently after the first split, so tier-1 successes in pass 1 do not prevent natural breaks in pass 2. Call `_split_message()` before each actual Discord API call and send chunks sequentially. Parse canonical external_user_id for routing; DB lookup via `source_repo.get_instance_mapping(source_id, external_user_id)` → mapping metadata → channel_id, thread_id; strip LLM tags; acquire per-channel lock; apply reply_to_id mapping (Task 8) — build `MessageReference` once (if `OutgoingMessage.reply_to_id` set) and pass as `reference=` to every chunk send; send via discord.py `channel.send(content, reference=...)` or `thread.send(content, reference=...)`. Pattern: Slack `send()` in `slack/adapter.py:393-509`, Telegram `send()` in `telegram.py:262-325`. | 1, 4, 8 | Valid mapping → all split chunks sent sequentially to the correct channel/thread with `reference=MessageReference(...)` applied when `OutgoingMessage.reply_to_id` is set; missing mapping → return False + log; circuit breaker open → return False + log; exactly 2000 characters sends one message; content with paragraph boundary (`\n\n`) near 2000 → splits at paragraph; content with no paragraph/line near 2000 but with sentence boundary (`. `) → splits at sentence; content with no paragraph/line/sentence boundary near 2000 but with a space → splits at word boundary; content with NO space within the last 2000 chars (e.g., 4000-char run of non-space characters) → hard cut at exactly 2000 |

## Inbound Metadata Schema

The `IncomingMessage.metadata` dict for Discord:

```python
metadata = {
    "discord": {
        "guild_id": "987654321098765432",     # None for DMs
        "guild_name": "My Server",
        "channel_id": "555444333222111333",
        "channel_name": "general",
        "channel_type": "text",                # "text", "dm", "thread", "news", "voice"
        "thread_id": "777888999000111222",     # None if not in thread
        "thread_name": "Discussion",           # None if not in thread
        "parent_channel_id": "555444333222111333", # For threads; None otherwise
        "user_id": "123456789012345678",
        "user_name": "username",               # Discord username (no discriminator in newer API)
        "user_display_name": "Display Name",
        "message_id": "111222333444555666",
        "is_dm": False,
    },
    "agent": self._default_agent,
}
```

## Outbound Routing Logic

`send(message)` flow:

```
1. Parse external_user_id:
   - dm:{user_id}           → DM routing
   - {guild}:{channel}      → channel routing
   - {guild}:{parent}:{thread} → thread routing

2. DB lookup: source_repo.get_instance_mapping(source_id, external_user_id)
   → mapping.mapping_metadata should contain:
     discord.channel_id, discord.thread_id (optional)

3. Resolve target:
   - If metadata has discord.channel_id → use it
   - If thread routing → use discord.thread_id or parse from external_user_id
   - Fetch channel/thread object via discord.py client.get_channel()

4. Acquire per-channel lock (Phase 3)
5. Strip LLM artifact tags
6. Apply reply_to_id mapping (Task 8): if OutgoingMessage.reply_to_id is set,
   build discord.MessageReference(message_id=int(reply_to_id), fail_if_not_exists=False);
   else leave reference as None. Build it ONCE outside the chunk loop.
7. Send each chunk: channel.send(content, reference=ref) or thread.send(content, reference=ref)
   (omit `reference` kwarg when ref is None to keep payload minimal)
8. Record success/failure to circuit breaker
```

## Coupling

- **Tight with:** Phase 1 — `send()` and `on_message` are methods on the adapter class created in Phase 1
- **Tight with:** Phase 3 — `send()` acquires per-channel locks and checks circuit breaker (implemented in Phase 3); for Phase 2, use stub locks (no-op `async with` or direct send without lock)
- **Loose with:** Phase 4 — tested in Phase 4

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Discord message content empty when MESSAGE_CONTENT intent not granted | High | Check in `on_ready` (Phase 1); `_normalize_incoming` handles empty content gracefully with placeholder + attachments |
| Thread routing fails if thread is archived between incoming and outgoing | Medium | Phase 3 `DiscordThreadManager` handles this; when a thread is archived, route outgoing messages to the parent channel instead |
| Message length exceeds Discord 2000 char limit | Medium | Phase 2 `_split_message()` applies a priority-ordered 5-tier fallback chain (paragraph → line → sentence → word → hard cut) per chunk, ensuring natural text breaks are preferred over mid-word cuts with hard-cut as the last resort, before sequential sends |
| Discord reply-to fails when the referenced message has been deleted | Low | Use `discord.MessageReference(..., fail_if_not_exists=False)` so the reply still posts as a normal message rather than raising |
| Cross-bot replies bypass the default bot-skip filter | Low | Default behavior skips all bot-authored messages; only bot IDs explicitly listed in `allowed_bot_ids` are processed |

## Exit Criterion

- Bot receives a DM → agent processes → response sent back to the DM channel
- Bot receives @mention in guild channel → agent processes → response sent to the same channel
- Bot receives non-mention guild message → no response (mention-gating works)
- Thread messages route to separate instances (unique external_user_id per thread)
- `send()` correctly resolves channel/thread from DB mapping metadata
- LLM artifact tags are stripped from outbound messages
- Image attachments are forwarded into `IncomingMessage.images` as URLs; messages containing only an attachment are NOT dropped (content gets a descriptive placeholder)
- Messages from guilds/channels not in `allowed_guilds`/`allowed_channels` are skipped (with debug log); cross-bot messages from bots not in `allowed_bot_ids` are skipped; default config (empty allowlists) permits everything
- Replies on inbound populate `IncomingMessage.reply_to_id` from `message_reference.message_id`; replies on outbound are threaded via Discord `message_reference` (passed as `reference=` kwarg to `channel.send()` / `thread.send()`)