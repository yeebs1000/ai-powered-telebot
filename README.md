# AI-Powered Telebot

**English** · [简体中文](README.zh-CN.md)

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Providers](https://img.shields.io/badge/AI-Gemini%20%7C%20OpenAI%20%7C%20Claude-brightgreen)

A Telegram group assistant that **isn't tied to one AI vendor**. Pick Gemini,
OpenAI, or Claude with a single environment variable — no code changes, no
rewrite when you change your mind, no lock-in.

It chats naturally, remembers what your group has talked about, and can search
the live web when a question needs current information.

## Why this one

- **Bring your own provider.** `AI_PROVIDER=gemini|openai|claude`. Every AI call
  goes through a small adapter in [`providers/`](providers/), so the bot never
  touches a vendor SDK directly. Adding a provider is one file.
- **Degrades honestly.** Claude has no embeddings API, so semantic memory turns
  itself off and says why, instead of erroring. Every optional feature behaves
  this way.
- **Remembers by meaning, not keywords.** Ask "where did we land on that
  restaurant" and it searches past messages by vector similarity.
- **Your data, your database.** Chat history lives in a Supabase project you
  own, behind row-level security with separate service and anon keys.

## Features

- **Conversational chat** — mention it in a group, or DM it.
- **Semantic memory search** — recall past discussion by meaning
  ("where did we land on…", "what was that place called").
- **Group summaries** — ask for a recap of recent activity.
- **Personality lookups** — "what do you think of \<name>" reads that person's
  message history and gives a light-hearted take.
- **Live web search** — scores, news, weather, prices, ongoing events.
- **Natural-language reminders** — "remind me to call mum at 6".
- **Interactive polls** — inline keyboard, five-minute window, live tally.
- **Image understanding** — send a photo with a question.

## Provider capabilities

| Provider | Chat | Embeddings (memory) | Images |
|---|---|---|---|
| `gemini` | ✅ | ✅ | ✅ |
| `openai` | ✅ | ✅ | ✅ |
| `claude` | ✅ | ❌ (no embeddings API) | ✅ |

Any OpenAI-compatible server (Ollama, LM Studio, vLLM) works through the
`openai` adapter by setting `OPENAI_BASE_URL`.

## Quickstart

```bash
git clone https://github.com/yeebs1000/ai-powered-telebot
cd ai-powered-telebot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in the keys below
.venv/bin/python main.py
```

1. **Bot token** — talk to [@BotFather](https://t.me/BotFather), `/newbot`.
   To let it read group messages, disable privacy mode:
   `/setprivacy` → your bot → **Disable**.
2. **Supabase** — create a free project, open **SQL Editor**, run
   [`supabase_schema.sql`](supabase_schema.sql).
3. **An AI key** — Gemini, OpenAI, or Anthropic. Just the one you picked.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | From @BotFather |
| `AI_PROVIDER` | yes | `gemini`, `openai`, or `claude` |
| `AI_MODEL` | no | Override the provider's default model |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | one of | Matching your provider |
| `SUPABASE_URL` | yes | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Server-side only — **keep private** |
| `SUPABASE_ANON_KEY` | yes | Used for RLS-protected operations |
| `OPENAI_BASE_URL` | no | Point at any OpenAI-compatible server |
| `TAVILY_API_KEY` | no | Enables live web search |

### On the two Supabase keys

The service-role key bypasses row-level security and must never reach a client
or a public repository; the anon key operates under RLS. `supabase_schema.sql`
creates the tables and the policies together — run it before first start.

## Deploying

It uses **long polling**, so it needs no inbound port, domain, or TLS, and runs
happily behind home-router NAT. Any always-on machine works: a VPS, a Raspberry
Pi, a spare laptop, or a PaaS worker (a `Procfile` is included).

> **One poller per bot token.** If the same token is already running somewhere
> else, Telegram returns `409 Conflict` and both instances misbehave.

## How it works

[`main.py`](main.py) is a single-file bot built on `python-telegram-bot`. AI
calls go through [`providers/`](providers/), so the rest of the code is
vendor-agnostic. Incoming messages are logged (with embeddings, when the
provider supports them) for later recall; a mention or DM triggers a reply,
routed by a strict-JSON intent classifier that decides whether the question
needs live web data or ordinary conversation.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Good first contributions: a new provider adapter in `providers/`, or a new
capability in `handle_message`.

## License

MIT — see [LICENSE](LICENSE).
