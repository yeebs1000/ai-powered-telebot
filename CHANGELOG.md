# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Removed
- **Group personality roasts.** The bot built a "funny, punchy, authentic"
  character read of a named member from 200 of their own messages and posted
  it to the group. The model cannot tell which of those messages were jokes,
  and the target never agreed to be profiled. Mildly amusing when it lands,
  unrecoverable when it does not. If it returns it is DM-only with explicit
  opt-in, which does not exist. "What do you think of X" now falls through to
  ordinary chat.

### Changed
- The clock is the system clock. It previously HEADed apple.com and parsed the
  Date header — a network round trip on the reply path, labelled NTP, failing
  whenever the internet did. The host keeps time already.

### Added
- `tests/t_hardening.py` — asserts neither of the above comes back.

## Unreleased

### Added
- `doctor.py` — checks the whole configuration and prints the fix. Validates
  the token against Telegram, warns when privacy mode would stop the bot
  reading group messages, confirms the model and embeddings actually answer,
  and never echoes a secret. `--offline` skips the network calls.
- `tests/run.sh` — offline suite by default (no network, no model, no keys),
  `--all` for the tests needing a live endpoint. CI now installs dependencies
  and runs it instead of only byte-compiling.
- `SETUP.md` / `SETUP.zh-CN.md` — step-by-step setup for someone with no prior
  experience, including the two things people miss: privacy mode, and running
  two copies on one token.
- `Dockerfile`, `docker-compose.yml`, `.dockerignore` — non-root, database on a
  named volume, healthcheck runs the doctor. Image built and run to verify.
- Providers: deepseek, openrouter, groq, together, local. Per-role models via
  `AI_MODEL_ROUTER` / `AI_MODEL_CHAT`, and a separate embeddings endpoint
  (`EMBED_BASE_URL`) so semantic memory survives a router that only serves chat.

### Fixed
- `TELEBOT_DB` defaulted to `/var/lib/telebot/telebot.db`, which a plain clone
  cannot write — first run failed for anyone not deploying as root. The default
  is now relative; the systemd unit pins the absolute path explicitly, so a
  deployment cannot silently inherit the relative one and start an empty
  database in its working directory.

### Removed
- Stock/forex/commodity lookups and `ALPHA_VANTAGE_KEY`. Price questions route
  to web search, which also covers crypto — something the market-data path
  never did.


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