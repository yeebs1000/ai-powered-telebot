# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Changed
- **Storage is now local SQLite instead of Supabase.** Chat logs, embeddings,
  and live polls live in one file (`TELEBOT_DB`, default
  `/var/lib/telebot/telebot.db`). The pgvector `match_chat_embeddings` RPC is
  replaced by brute-force cosine similarity over numpy, which needs no
  extension or index at group-chat volumes. `supabase` is no longer a
  dependency, and `SUPABASE_URL`/`SUPABASE_KEY` are gone.

### Added
- `store.py` — the SQLite store, with `tests/t_store.py` covering ordering,
  case-insensitive sender match, cosine ranking, dimension-mismatch skipping,
  and poll vote changes.
- `project_vault.py` — projects the store into an Obsidian vault as daily
  notes (deterministic counts and terms, plus an optional labelled model
  recap that is omitted rather than faked when the model is unreachable).
- `deploy/telebot-vault.{service,timer}` — daily projection at 03:15.


## [Unreleased]

### Added
- Renamed the project to **AI-Powered Telebot** to reflect multi-provider
  support.
- `providers/` — a small adapter layer (`AIProvider` / `ChatSession` in
  `providers/base.py`) so the bot can run on Gemini, OpenAI, or Claude via
  a single `AI_PROVIDER` env var, with an optional `AI_MODEL` override.
  Embeddings-dependent features (background message embedding, semantic
  memory search) are automatically skipped when the active provider doesn't
  support embeddings (currently Claude).
- README with full setup/deploy instructions, `.env.example`, LICENSE (MIT),
  CONTRIBUTING.md, and a minimal CI workflow for open-sourcing.
- `supabase_schema.sql` — documents the tables (`group_chat_logs`,
  `group_embeddings`, `active_polls`) and the `match_chat_embeddings` RPC
  function the bot depends on, previously undocumented.
- CONTRIBUTING.md section on adding a new AI provider.

### Changed
- `main.py` no longer imports any AI vendor SDK directly — all model calls
  go through `providers.get_provider()`.

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