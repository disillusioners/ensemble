# Feature Comparison: Slack vs Telegram

## Overview

This document compares Slack and Telegram features relevant to the ensemble source adapter. It identifies what maps directly, what's new in Slack, and what Telegram features don't exist in Slack.

## Message Fundamentals

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Text messages | ✅ | ✅ | 1:1 | Both support text; Slack uses mrkdwn, Telegram uses HTML |
| Images | ✅ | ✅ | 1:1 | Slack uses `url_private` (auth-required), Telegram uses public URLs |
| Documents | ✅ | ✅ | 1:1 | Slack `files.upload` v2 API; Telegram `sendDocument` |
| Message editing | ✅ | ✅ | Partial | Both can edit; Slack sends `message_changed` event subtype |
| Message deletion | ✅ | ✅ | Partial | Both can delete; Slack sends `message_deleted` event subtype |
| Reply to message | ✅ | ✅ | Different | Telegram: reply_to_message_id; Slack: thread_ts (threaded) |
| Bot mentions | ✅ (via /command) | ✅ (via @mention) | Different | Slack app_mention is a distinct event type |
| Message formatting | HTML | mrkdwn | Converted | Need formatter for both directions |

## Conversation Models

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Direct Messages | ✅ (private chat) | ✅ (DM/im) | 1:1 | Both 1:1 conversations |
| Group chats | ✅ (groups) | ✅ (channels) | 1:1 | Both multi-user; Slack channels are persistent |
| Supergroups | ✅ | ✅ (channels, larger) | 1:1 | Slack channels have no member limit on paid plans |
| Private groups | ✅ | ✅ (private channels) | 1:1 | Slack calls them "private channels" (not "groups") |
| **Threads** | ❌ | ✅ | **NEW** | Slack-specific: threaded replies, isolated conversations |
| Multi-party IM | ❌ | ✅ (mpim) | **NEW** | Group DMs in Slack |
| Shared channels | ❌ | ✅ (shared channels) | **NEW** | Cross-workspace channels (Slack Connect) |

## Interactive Features

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Commands | ✅ (bot commands) | ✅ (slash commands) | 1:1 | Both `/command` syntax; Slack requires app config |
| **Buttons** | ❌ (InlineKeyboard) | ✅ (Block Kit buttons) | **Different** | Slack Block Kit is richer, with action_id routing |
| **Dropdowns** | ❌ | ✅ (select menus) | **NEW** | Static/external/user/channel selects |
| **Modals** | ❌ | ✅ | **NEW** | Rich interactive forms (multi-step, input validation) |
| **App Home** | ❌ | ✅ | **NEW** | Per-user persistent landing tab |
| **Canvas** | ❌ | ✅ | **NEW** | Slack's collaborative document feature |
| Reactions | ❌ | ✅ | **NEW** | Emoji reactions on messages |
| Typing indicator | ✅ | ❌ | Telegram-only | Slack has no typing indicator API |
| Read receipts | ✅ (in channels) | ✅ (paid plans) | 1:1 | Not used in adapter |

## Rich Media

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Photos | ✅ | ✅ | 1:1 | Both support image upload/display |
| Videos | ✅ | ✅ | 1:1 | Video files in Slack |
| Audio | ✅ | ✅ | 1:1 | Audio/voice messages |
| Stickers | ✅ | ❌ | Telegram-only | Slack has custom emoji but not sticker packs |
| **Block Kit** | ❌ | ✅ | **NEW** | Rich layout: sections, fields, dividers, images, actions |
| **Attachments** | ❌ (legacy) | ✅ | **NEW** | Secondary attachments with fields/actions |
| Link unfurling | ✅ | ✅ | 1:1 | Both auto-expand links |
| Code blocks | ✅ | ✅ | 1:1 | Both support code formatting |

## Channel/Workspace Management

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| **Workspaces** | ❌ | ✅ | **NEW** | Slack has team/workspace concept |
| **Multi-workspace** | ❌ | ✅ | **NEW** | Single app can serve multiple workspaces via OAuth |
| **OAuth install** | ❌ (BotFather) | ✅ | **NEW** | Slack uses OAuth for app installation |
| Channel bookmarks | ❌ | ✅ | **NEW** | Links pinned to channel header |
| Pins | ❌ | ✅ | **NEW** | Pinned messages in channel |
| Stars | ❌ | ✅ | Partial | User can star channels/messages (not API-relevant) |
| Channel topics | ❌ | ✅ | Partial | Slack channel topic/purpose |

## User & Identity

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| User profiles | ✅ (basic) | ✅ (rich) | Different | Slack profiles have more fields (title, phone, etc.) |
| User presence | ❌ | ✅ | **NEW** | away/auto/manual/dnd |
| **User groups** | ❌ | ✅ | **NEW** | Named groups of users (e.g., @engineering) |
| Bot identity | ✅ (bot user) | ✅ (bot user) | 1:1 | Both have distinct bot identity |
| Admin detection | ❌ | ✅ | **NEW** | Can check if user is workspace admin |
| Timezone | ❌ | ✅ | **NEW** | Per-user timezone awareness |

## Connection Model

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Long polling | ✅ (getUpdates) | ❌ | Telegram-only | Slack doesn't support polling |
| Webhooks | ✅ (setWebhook) | ✅ (Events API) | 1:1 | Both support HTTP webhooks |
| **Socket Mode** | ❌ | ✅ | **NEW** | WebSocket-based, no public endpoint needed |
| Rate limits | 30 msg/sec (global) | Tiered per-method | **Different** | Slack has complex tiered rate limits |
| File size limits | 50MB | 1GB (paid) | Different | Slack supports larger files on paid plans |
| Message length | 4096 chars | 40000 chars | Different | Slack allows much longer messages |

## Security & Access

| Feature | Telegram | Slack | Mapping | Notes |
|---------|----------|-------|---------|-------|
| Webhook secret | ✅ (secret_token) | ✅ (request signing) | 1:1 | Both verify incoming requests |
| Bot permissions | ✅ (all or nothing) | ✅ (granular scopes) | **Different** | Slack has fine-grained OAuth scopes |
| Token types | bot_token | bot_token + app_token | **Different** | Slack needs both xoxb and xapp tokens |
| Enterprise Grid | ❌ | ✅ | **NEW** | Slack enterprise features (org-level apps) |

## Summary Statistics

| Category | Maps 1:1 | Slack-Only (NEW) | Telegram-Only |
|----------|---------|-------------------|---------------|
| Message types | 5 | 1 (Block Kit) | 1 (Stickers) |
| Conversation models | 3 | 3 (Threads, MPIM, Shared) | 0 |
| Interactive features | 1 | 6 (Buttons, Menus, Modals, Home, Canvas, Reactions) | 0 |
| Channel management | 0 | 5 (Workspaces, OAuth, Bookmarks, Pins, Topics) | 0 |
| User features | 2 | 4 (Presence, Groups, Admin, Timezone) | 0 |
| Connection | 1 | 1 (Socket Mode) | 1 (Polling) |
| **Total** | **12** | **20** | **2** |

## Implications for Implementation

### High-Value New Features (Must Have)
1. **Socket Mode** — Simpler deployment, no public endpoint
2. **Threads** — Natural mapping to instance isolation
3. **Slash commands** — `/new`, `/agent`, `/status`
4. **Reactions** — Visual feedback (👀 processing, ✅ done)
5. **Block Kit** — Rich message formatting

### Medium-Value Features (Should Have)
6. **Buttons/menus** — Interactive confirmation, agent selection
7. **Modals** — Settings forms, input collection
8. **App Home** — Status dashboard
9. **File sharing** — Bidirectional file transfer
10. **User groups** — Team-based routing

### Lower-Value Features (Nice to Have)
11. **Channel bookmarks** — Agent can pin important resources
12. **Canvas** — Collaborative documents
13. **Multi-workspace OAuth** — Self-service installation
14. **Presence awareness** — Route only to active users
15. **Pins** — Pin important agent responses
