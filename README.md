# Telebot

An intelligent Telegram group assistant powered by Google Gemini. It chats
naturally, remembers what your group talks about, and can pull live stock,
forex, commodity, and web-search data into the conversation.

## Features

- **Conversational chat** — mention the bot (or DM it) and it replies in a
  casual, friend-like tone, powered by Gemini.
- **Group chat logging & summaries** — ask it to "summarize" and it recaps
  recent group activity from a Supabase-backed log.
- **Personality lookups** — "what do you think of \<name>" pulls that
  person's message history and gives a light-hearted read on them.
- **Semantic memory search** — "where did we..." / "search memory ..."
  searches past messages by meaning (vector embeddings), not just keywords.
- **Natural-language reminders** — "remind me to ... at 6pm" schedules a
  one-off reminder via the bot's job queue.
- **Live polls** — `poll: Question | Option 1 | Option 2` creates an inline
  voting poll that auto-closes and tallies after 5 minutes.
- **Live market data** — ask about a stock, forex pair, or commodity and it
  fetches a real-time quote (Alpha Vantage).
- **Live web search** — sports scores, breaking news, and anything else that
  needs current information is routed to a web search (Tavily) instead of
  the model's static knowledge.
- **Image understanding** — send a photo in a chat where the bot is
  mentioned and it will analyze it.

An intent classifier (a small Gemini call) decides, per message, whether to
route to chat, market data, or web search — no slash commands needed for
those features.

## Requirements

- Python 3.11+
- A [Telegram bot token](https://core.telegram.org/bots#how-do-i-create-a-bot)
  from [@BotFather](https://t.me/BotFather)
- A [Gemini API key](https://aistudio.google.com/apikey)
- A free [Supabase](https://supabase.com) project (used for chat logs,
  semantic memory, and live polls)
- Optional: a [Tavily](https://tavily.com) API key for live web search
- Optional: an [Alpha Vantage](https://www.alphavantage.co/support/#api-key)
  API key for stock/forex/commodity quotes

## Setup

1. **Clone and install dependencies**

   ```bash
   git clone https://github.com/yeebs1000/Telebot.git
   cd Telebot
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set up Supabase**

   Create a project at [supabase.com](https://supabase.com), open the
   **SQL Editor**, and run the contents of [`supabase_schema.sql`](supabase_schema.sql).
   This creates the tables and RPC function the bot needs (chat logs,
   embeddings, and live polls).

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   Fill in `.env` with your keys:

   | Variable             | Required | Purpose                                  |
   |----------------------|----------|-------------------------------------------|
   | `TELEGRAM_BOT_TOKEN` | Yes      | Bot auth token from @BotFather            |
   | `GEMINI_API_KEY`     | Yes      | Chat, routing, embeddings                 |
   | `SUPABASE_URL`       | Yes      | Supabase project URL                      |
   | `SUPABASE_KEY`       | Yes      | Supabase service/anon key                 |
   | `TAVILY_API_KEY`     | No       | Enables live web search                   |
   | `ALPHA_VANTAGE_KEY`  | No       | Enables stock/forex/commodity quotes       |

4. **Run it**

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

## How it works

`main.py` is a single-file bot built on `python-telegram-bot`. Every incoming
message goes through `handle_message`, which:

1. Logs non-directed group messages (and their embeddings) to Supabase in
   the background, for later summaries/search.
2. If the bot is mentioned (or it's a DM), checks for built-in features
   (summarize, personality lookup, reminders, memory search, polls) by
   simple keyword matching.
3. Otherwise calls `classify_intent()` — a lightweight Gemini call that
   returns structured JSON to route the message to a stock/forex/commodity
   lookup, a live web search, or plain conversation.
4. Sends the assembled context (plus a live timestamp, and an image if one
   was attached) to a per-chat Gemini chat session and replies.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
