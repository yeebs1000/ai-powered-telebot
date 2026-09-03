# AI-Powered Telebot

**English** · [简体中文](README.zh-CN.md)

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Providers](https://img.shields.io/badge/AI-OpenAI%20%7C%20Gemini%20%7C%20Claude%20%7C%20DeepSeek%20%7C%20OpenRouter%20%7C%20local-brightgreen)

A Telegram group assistant that runs on **whatever model you want** — OpenAI,
Gemini, Claude, DeepSeek, an OpenRouter/Groq/Together router, or a local Ollama
— chosen with one environment variable. No vendor lock-in, no cloud database,
no account to sign up for beyond the model itself.

It learns how your group talks, remembers what was said, and reacts like
someone who is actually reading the chat.

## Why this one

- **Any provider, one variable.** `AI_PROVIDER=deepseek`. Every AI call goes
  through an adapter in [`providers/`](providers/), so nothing else in the
  codebase knows which vendor you picked.
- **Different models for different jobs.** The cheap fast model routes intent;
  the good model writes the reply. On a router that split is most of the bill.
  See [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **No database to provision.** Chat history, embeddings, members and polls
  live in one local SQLite file. Nothing to sign up for, nothing to pay for,
  and your group's messages stay on your machine.
- **Reacts to meaning, not keywords.** A keyword table gives *"my grandad
  passed away last night"* a 🎉, because "passed" looks like congratulations.
  This one compares meaning in embedding space and gets 😢.
- **Learns your group's voice.** It measures what each person says *more than
  everyone else does*, plus habits like message length and emoji use, and
  writes in that register. Aggregated from the log, not fine-tuned — so it
  improves with every message and you can read the profile yourself.
- **Runs entirely offline if you want.** Point it at Ollama and nothing leaves
  the machine.

## Features

- **Conversational chat** — mention it in a group, or DM it.
- **Semantic memory** — "where did we land on that restaurant" searches past
  messages by meaning.
- **Catch me up** — anchored to *your* last message, so what you missed is
  actually what you missed. Someone who spoke five minutes ago is told so.
- **Group summaries** — a recap of recent conversation.
- **Knows who's who** — members are tracked by Telegram user ID, matched
  fuzzily by name (`yuanbing` finds `Yuan Bing`), and it refuses to guess when
  two people are equally close. Can't place someone? Reply to them and say
  `@yourbot this is Marcus` — the name sticks.
- **Live web search** — scores, news, weather, prices, ongoing events.
- **Reminders, polls, images, voice notes.**
- **Optional reference folder** — a read-only directory of notes it may draw
  on when replying.

## Quickstart

> New to this? **[SETUP.md](SETUP.md)** is a step-by-step guide that assumes no prior experience.


```bash
git clone https://github.com/yeebs1000/ai-powered-telebot
cd ai-powered-telebot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python main.py
```

You need two things in `.env`:

1. **A bot token** — [@BotFather](https://t.me/BotFather) → `/newbot`.
   To let it read group messages: `/setprivacy` → your bot → **Disable**.
2. **A provider** — pick one:

```bash
# a router, any model you like
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5

# or cheap and good
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...

# or free and private
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
OPENAI_REASONING_EFFORT=none
```

That's it. The database creates itself on first run.

> **One poller per bot token.** If the same token is running anywhere else,
> Telegram returns `409 Conflict` and both instances misbehave.

Not sure it's configured right? `python doctor.py` checks everything and
prints the fix.

## Providers and models

| `AI_PROVIDER` | Key | Default model | Embeddings |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | ✅ |
| `gemini` | `GEMINI_API_KEY` | provider default | ✅ |
| `claude` | `ANTHROPIC_API_KEY` | provider default | ❌ |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | ❌ |
| `openrouter` | `OPENROUTER_API_KEY` | set `AI_MODEL` | ❌ |
| `groq` | `GROQ_API_KEY` | set `AI_MODEL` | ❌ |
| `together` | `TOGETHER_API_KEY` | set `AI_MODEL` | ✅ |
| `local` | none | set `AI_MODEL` | via `EMBED_*` |

Routers proxy chat, not embeddings — so semantic memory would switch off. Point
it at a local Ollama and keep it, for free:

```bash
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL=nomic-embed-text
```

**[docs/PROVIDERS.md](docs/PROVIDERS.md)** covers all of this properly:
per-role models, why router models need to be capable enough for JSON, keeping
memory alive, and the reasoning-model latency trap.

## Deploying

Long polling — no inbound port, no domain, no TLS. It runs behind home-router
NAT on anything always-on: a VPS, a Raspberry Pi, a spare laptop, a mini PC. A
systemd unit is in [`deploy/`](deploy/), and a `Procfile` is included for PaaS
workers.

## How it works

[`main.py`](main.py) is the bot, built on `python-telegram-bot`. Around it:

| Module | Role |
|---|---|
| [`providers/`](providers/) | Vendor adapters — the only code that knows about an AI API |
| [`store.py`](store.py) | SQLite: messages, embeddings, members, polls |
| [`profiles.py`](profiles.py) | Per-member style, aggregated from the log |
| [`vault.py`](vault.py) | Optional read-only reference notes |

Each message: log it (with an embedding) if it isn't directed at the bot →
and if it is, route the intent with one strict-JSON call, then answer. The bot
says nothing unless it is spoken to.

## Testing

```bash
for t in tests/t_*.py; do .venv/bin/python "$t"; done
```

Covers provider resolution and error messages, message ordering, fuzzy name
matching and its refusal to guess, identity binding, style-profile
distinctiveness, per-person catch-up, promises surviving a restart, backup and
restore, vector round-trips, and reference-folder scoping.

## Privacy

Chat history, embeddings and polls live in one local SQLite file, chmod'd
`0600` — SQLite would otherwise create it world-readable, and it holds other
people's messages. Only group messages **not** directed at the bot are logged;
DMs are not. With `AI_PROVIDER=local`, nothing leaves your machine at all.

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Good first contributions: another OpenAI-compatible provider (one line in
`providers/__init__.py`), or a new capability in `handle_message`.

## License

MIT — see [LICENSE](LICENSE).
