# Contributing to Telebot

Thanks for considering a contribution. A few things to know before you start.

## Setup

Follow the steps in [README.md](README.md): create a Supabase project, run
`supabase_schema.sql`, copy `.env.example` to `.env`, and fill in at least
the required keys (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `SUPABASE_URL`,
`SUPABASE_KEY`).

## Running it locally

```bash
python main.py
```

Message your bot in a private chat, or add it to a test Telegram group, to
exercise changes end-to-end.

## Testing changes — please read this before opening a PR

This bot calls paid, metered APIs (Gemini, and optionally Tavily / Alpha
Vantage) for almost everything it does. **Don't spam test messages against
a live bot token just to "check" a change** — each mention triggers at least
one Gemini call, often more (routing classification, embeddings, etc.).

- For logic-only changes (parsing, formatting, routing keyword matching),
  test the relevant function directly in a Python shell rather than running
  the full bot.
- If you need to verify an end-to-end flow, use a fresh `GEMINI_API_KEY`
  with the free tier rather than a production key.
- There is no automated test suite — CI only does a Python syntax/import
  compile check (`python -m compileall`). Manual verification against a
  test bot/group is expected before opening a PR.

## Code style

- Single-file bot (`main.py`) — keep new features as additional `elif`
  branches in `handle_message` or as new async helper functions, following
  the existing pattern (feature comment headers, `asyncio.to_thread()` for
  any blocking Supabase/Gemini calls).
- No hardcoded secrets, ever — everything configurable goes through
  `os.getenv()` and gets documented in `.env.example`.

## Submitting changes

1. Fork the repo and create a feature branch.
2. Make your changes and verify manually against a test bot.
3. Open a PR describing what changed and why, and how you tested it.
