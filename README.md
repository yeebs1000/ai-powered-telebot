# AI-Powered Telebot

An intelligent Telegram group assistant that works with the AI provider of
your choice — Gemini, OpenAI, or Claude — picked with a single environment
variable. It chats naturally, remembers what your group talks about, and can
pull live stock, forex, commodity, and web-search data into the conversation.

## Features

- **Conversational chat** — mention the bot (or DM it) and it replies in a
  casual, friend-like tone.
- **Bring your own AI provider** — switch between Gemini, OpenAI, and Claude
  with one env var; no code changes needed. See
  [Choosing your AI provider](#choosing-your-ai-provider) below.
- **Live reactions** — the bot drops an emoji reaction (🤣🎉🔥❤😢🤯🙏💯)
  on group messages that clearly warrant one, mentioned or not, so it feels
  present in the chat. No AI call — a lightweight local heuristic.
- **Voice messages** — send a voice note and the bot transcribes it, then
  treats it exactly like a typed message (so "remind me to call mum at 6",
  spoken, still schedules the reminder). Requires an audio-capable provider
  (Gemini or OpenAI).
- **Group chat logging & summaries** — ask for a recap ("summarize", "what
  did I miss", "catch me up") and it recaps recent group activity from a
  local SQLite log.
- **Personality lookups** — "what do you think of \<name>" (or any natural
  phrasing) pulls that person's message history and gives a light-hearted
  read on them.
- **Semantic memory search** — ask it to recall a past topic ("where did we
  land on...", "what was that restaurant") and it searches past messages by
  meaning (vector embeddings), not just keywords. Requires a provider that
  supports embeddings (Gemini or OpenAI).
- **Natural-language reminders** — "remind me to ... at 6pm" (or "ping me
  before the standup") schedules a one-off reminder via the bot's job queue.
- **Live polls** — ask for a vote in plain language ("let's vote on lunch,
  thai or korean") or use `poll: Question | Option 1 | Option 2`; either
  creates an inline poll that auto-closes and tallies after 5 minutes.
- **Live market data** — ask about a stock, forex pair, or commodity and it
  fetches a real-time quote (Alpha Vantage).
- **Live web search** — sports scores, breaking news, and anything else that
  needs current information is routed to a web search (Tavily) instead of
  the model's static knowledge.
- **Image understanding** — send a photo in a chat where the bot is
  mentioned and it will analyze it.

A single AI router decides, per message, which action to take — chat,
market data, web search, summary, reminder, poll, personality read, or
memory search — and extracts its parameters. There are no rigid keywords or
slash commands; it reads intent from natural phrasing.

## Choosing your AI provider

Set `AI_PROVIDER` in `.env` to one of `gemini`, `openai`, or `claude`, and
fill in the matching API key. Everything else in the bot — chat, intent
routing, reminders, market data, polls — works identically regardless of
provider.

| Provider | `AI_PROVIDER` value | API key env var     | Chat | Intent routing | Embeddings / semantic memory |
|----------|----------------------|----------------------|------|-----------------|-------------------------------|
| Gemini   | `gemini`             | `GEMINI_API_KEY`     | ✅   | ✅ (native JSON mode) | ✅ (native 768-dim)            |
| OpenAI   | `openai`              | `OPENAI_API_KEY`     | ✅   | ✅ (native JSON mode) | ✅ (768-dim via `dimensions`)  |
| Claude   | `claude`              | `ANTHROPIC_API_KEY`  | ✅   | ✅ (prompted JSON)    | ❌ — no embeddings API; semantic memory search is automatically disabled |

You can also override the default model per provider with `AI_MODEL`
(defaults: `gemini-3.1-flash-lite`, `gpt-4o-mini`, `claude-sonnet-4-6`).

Only the SDK for your chosen provider is actually used at runtime — the
other two packages in `requirements.txt` just sit unused, so switching
providers later is a one-line `.env` change, no reinstall required.

Want to add a provider that isn't listed (Mistral, Llama, a local model,
etc.)? See [Adding a new AI provider](CONTRIBUTING.md#adding-a-new-ai-provider)
in CONTRIBUTING.md — it's a single new file implementing one small interface.

## Requirements

- Python 3.11+
- A [Telegram bot token](https://core.telegram.org/bots#how-do-i-create-a-bot)
  from [@BotFather](https://t.me/BotFather)
- An API key for whichever AI provider you pick:
  [Gemini](https://aistudio.google.com/apikey),
  [OpenAI](https://platform.openai.com/api-keys), or
  [Anthropic](https://console.anthropic.com/settings/keys)
- Optional: a [Tavily](https://tavily.com) API key for live web search
- Optional: an [Alpha Vantage](https://www.alphavantage.co/support/#api-key)
  API key for stock/forex/commodity quotes

## Setup

1. **Clone and install dependencies**

   ```bash
   git clone https://github.com/yeebs1000/ai-powered-telebot.git
   cd ai-powered-telebot
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables**

   No database to provision: chat logs, embeddings, and live polls live in a
   local SQLite file, created on first run. It defaults to
   `/var/lib/telebot/telebot.db`; set `TELEBOT_DB` to put it elsewhere.

   ```bash
   cp .env.example .env
   ```

   Fill in `.env` with your keys:

   | Variable             | Required | Purpose                                   |
   |----------------------|----------|---------------------------------------------|
   | `AI_PROVIDER`        | Yes      | `gemini`, `openai`, or `claude`             |
   | `AI_MODEL`           | No       | Override the default model for your provider |
   | `TELEGRAM_BOT_TOKEN` | Yes      | Bot auth token from @BotFather              |
   | `GEMINI_API_KEY`     | If using Gemini | Chat, routing, embeddings             |
   | `OPENAI_API_KEY`     | If using OpenAI | Chat, routing, embeddings             |
   | `ANTHROPIC_API_KEY`  | If using Claude | Chat, routing                         |
   | `TELEBOT_DB`         | No       | SQLite path (default `/var/lib/telebot/telebot.db`) |
   | `TAVILY_API_KEY`     | No       | Enables live web search                     |
   | `ALPHA_VANTAGE_KEY`  | No       | Enables stock/forex/commodity quotes         |

3. **Run it**

   ```bash
   python main.py
   ```

   Add the bot to a Telegram group (or message it directly), mention it by
   `@username`, and start chatting.

## Deploying

The repo includes a `Procfile` and `runtime.txt` for platforms like
[Railway](https://railway.app) or Heroku-style buildpacks:

1. Push the repo to your deployment platform.
2. Set the same environment variables from `.env.example` in the platform's
   config/secrets UI.
3. Deploy — it runs as a long-lived worker process (`python main.py`),
   polling Telegram for updates.

### Self-hosting on a Linux mini PC (with a local LLM)

Because the bot uses Telegram **long-polling**, it needs no inbound ports,
domain, or TLS — it dials out to Telegram, so it works behind home-router NAT.

1. **Install Ollama and pull a model** (any OpenAI-compatible server works —
   LM Studio, vLLM, llama.cpp — but Ollama is simplest):

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull qwen2.5:7b-instruct        # good balance; needs ~6 GB RAM
   # On a slower/low-RAM box, try a smaller model:
   # ollama pull llama3.2:3b-instruct
   ```

2. **Point the bot at it** in `.env`:

   ```bash
   AI_PROVIDER=openai
   OPENAI_BASE_URL=http://localhost:11434/v1
   OPENAI_API_KEY=ollama
   AI_MODEL=qwen2.5:7b-instruct
   # Optional: local semantic memory (nomic-embed-text is 768-dim, matches the schema)
   # OPENAI_EMBED_MODEL=nomic-embed-text   # then: ollama pull nomic-embed-text
   # Optional: local voice transcription (independent of the chat model)
   # WHISPER_MODEL=base                    # then: pip install faster-whisper
   ```

3. **Install the venv and run it as a service** (auto-restart, starts on boot):

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   # Edit the User/paths in deploy/telebot.service, then:
   sudo cp deploy/telebot.service /etc/systemd/system/telebot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now telebot.service
   journalctl -u telebot -f
   ```

**Things to know when running a local model on CPU:**

- **Run only ONE instance per bot token.** If it's still running on Railway,
  stop it first — two pollers on one token causes `409 Conflict`.
- **JSON routing needs a capable model.** The intent router asks the model for
  strict JSON; small models (≤3B) can be unreliable at this, and a bad parse
  falls back to plain chat (features silently stop firing). Prefer a 7B+
  instruct model, and a recent Ollama that supports `response_format`.
- **Context window.** Summaries pull up to 500 messages; that can exceed a
  local model's default context (2k–8k tokens). If summaries look truncated,
  lower the limit in `main.py` or raise `num_ctx` via an Ollama Modelfile.
- **Voice notes** — Ollama can't run Whisper, so transcribe locally instead:
  `pip install faster-whisper` and set `WHISPER_MODEL=base` (or `small`). This
  runs independently of the chat model, so it works even with a local Ollama
  chat backend. Leave `WHISPER_MODEL` blank to disable. The first voice note
  after startup loads the model (a few seconds), then it's cached.
- **Keep Ollama on localhost** (its default). Don't expose port 11434.

## How it works

`main.py` is a single-file bot built on `python-telegram-bot`. AI calls go
through a small adapter layer in `providers/` (see
[providers/base.py](providers/base.py)) so the rest of the bot never talks
to a specific vendor SDK directly. Every incoming message goes through
`handle_message`, which:

1. Reacts to group messages with an emoji when a local heuristic finds a
   clear match (no AI call), and logs non-directed group messages (and their
   embeddings, if the active provider supports them) to the local SQLite
   store in the background, for later summaries/search.
2. Transcribes a voice note to text first (if the provider supports audio),
   so the rest of the pipeline treats speech identically to typing.
3. If the bot is mentioned (or it's a DM), calls `route_message()` — one
   lightweight AI call that returns structured JSON naming the action
   (summary, personality read, reminder, memory search, poll, stock/forex/
   commodity lookup, web search, or plain chat) *and* its parameters. No
   keyword matching — intent is read from natural phrasing.
4. Sends the assembled context (plus a live timestamp, and an image if one
   was attached) to a per-chat AI session and replies.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
