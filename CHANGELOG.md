# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- README with full setup/deploy instructions, `.env.example`, LICENSE (MIT),
  CONTRIBUTING.md, and a minimal CI workflow for open-sourcing.
- `supabase_schema.sql` — documents the tables (`group_chat_logs`,
  `group_embeddings`, `active_polls`) and the `match_chat_embeddings` RPC
  function the bot depends on, previously undocumented.

### Fixed
- `requirements.txt` was corrupted (UTF-16 encoded with embedded nulls),
  causing `pip install -r requirements.txt` to fail; re-saved as plain UTF-8.

## [Earlier history]
Prior to open-sourcing, the bot went through several rounds of hardening
(see git log for full detail):
- Async/non-blocking conversion of all Supabase, Gemini, and HTTP calls so
  the event loop never blocks under concurrent group messages.
- Strict JSON-mode intent routing (stock/forex/commodity/web-search/chat)
  with few-shot examples to fix unreliable classification.
- Live NTP-based timestamping, natural-language reminders via the bot's job
  queue, semantic long-term memory search (pgvector), and interactive
  live polls with inline keyboards.
- Migration of group chat logs from local/ephemeral storage to a permanent
  Supabase-backed store, with explicit env var mapping for Railway
  deployment.
