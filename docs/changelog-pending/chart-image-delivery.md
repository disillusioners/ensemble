# Chart-image delivery — release notes (pending)

This file holds the EXACT `### Added` block to be sliced into `CHANGELOG.md` under `[Unreleased]` at the chart-image-delivery release cut. Authored at Phase D execution; the release coordinator copies the block below verbatim (no edits).

```
- **Chart-image delivery** (`feature/chart-image-delivery`, merge `feature/chart-image-delivery`). Rendered Mermaid diagrams are now delivered as native image attachments in chat-source dispatches (Discord attachments, Telegram `sendPhoto`/`sendDocument`, Slack `files.uploadV2`) instead of Mermaid code blocks. Charter (the Mermaid agent) renders to PNG at validation time and emits a source-agnostic `<!-- ens-img:chart-render:<id> -->` reference marker; the chat-source dispatcher extracts the marker, resolves the PNG via `TmpImageStore.open_with_meta`, and uploads via the per-adapter native API. HTTP-API callers see the marker verbatim and may fetch the PNG via `GET /api/tmp_images/<id>`. Internal transport (marker + tmp_images substrate) is unchanged; degraded delivery is text-only with the Mermaid block intact. **Operator action required for Slack delivery:** grant the `files:write` OAuth scope in your Slack app config and reinstall the app; until granted, Slack text-only delivery with WARN-once log per channel. See `docs/sources/slack-setup.md` scope table and `.agents/shared/planning/chart-image-delivery/release-report.md` for the full operator runbook.
```

## Integration note

- Branch: `feature/chart-image-delivery` (HEAD at execution time recorded in `release-report.md` §1)
- Integration is pending — `giter` owns the merge to `latest` (see `wt.claim.chart-img` in the shared meta-kv)
- Release cut (version bump + this block slice into `CHANGELOG.md` `[Unreleased]` + tag) follows the project's `upgrade_policy` ceremony