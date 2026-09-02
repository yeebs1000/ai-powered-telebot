# Setup

[English](SETUP.md) · [简体中文](SETUP.zh-CN.md)

This assumes no prior experience. It takes about ten minutes.

If anything goes wrong, run `python doctor.py` — it checks the whole
configuration and tells you what to fix.

---

## 1. Install Python

You need **Python 3.11 or newer**.

- **Windows / macOS** — [python.org/downloads](https://www.python.org/downloads/).
  On Windows, tick **"Add Python to PATH"** during install.
- **Linux** — usually already there. Check with `python3 --version`.

## 2. Get the code

```bash
git clone https://github.com/yeebs1000/ai-powered-telebot
cd ai-powered-telebot
```

No git? Use the green **Code** button → **Download ZIP**, then unzip and open a
terminal in that folder.

## 3. Install the dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Windows use `.venv\Scripts\pip` instead of `.venv/bin/pip`.

## 4. Create your bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, pick a name and a username.
3. He replies with a **token** — a long string like `123456789:AAE...`.
   Keep it private; anyone with it controls your bot.

**Then turn privacy mode off**, or the bot cannot read group messages and will
look broken:

```
/setprivacy  →  choose your bot  →  Disable
```

If the bot is already in a group, remove and re-add it after changing this —
the setting only applies from when it joins.

## 5. Choose how it thinks

Copy the example config:

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in the token, then **one** of these:

**Cheapest paid option**

```bash
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
```

**Any model you like, one key** ([openrouter.ai](https://openrouter.ai/keys))

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5
```

**Free and private** — needs [Ollama](https://ollama.com) installed

```bash
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
OPENAI_REASONING_EFFORT=none
```

Then `ollama pull qwen3.5:9b`.

More detail, including using different models for different jobs:
[docs/PROVIDERS.md](docs/PROVIDERS.md).

## 6. Check it before you start

```bash
.venv/bin/python doctor.py
```

It verifies the token against Telegram, checks your provider and model
actually answer, confirms the database is writable, and warns about the two
things people most often miss. Fix anything it marks `FAIL`, then re-run.

## 7. Run it

```bash
.venv/bin/python main.py
```

Add the bot to a group, mention it by name, and it should reply.

To stop it, press Ctrl-C.

---

## Keeping it running

The bot must be running for it to answer. Options:

**A spare machine, always on** — a mini PC, an old laptop, a Raspberry Pi. Use
the systemd unit in [`deploy/`](deploy/):

```bash
sudo cp deploy/telebot.service /etc/systemd/system/
# edit the User= and paths inside it first
sudo systemctl daemon-reload
sudo systemctl enable --now telebot
journalctl -u telebot -f
```

**Docker** — see [`docker-compose.yml`](docker-compose.yml):

```bash
docker compose up -d
docker compose logs -f
```

**A cloud worker** — a `Procfile` is included for platforms that use one.

> **Only run one copy at a time.** Two instances sharing a bot token make
> Telegram return `409 Conflict`, and both misbehave. This is the most common
> problem after moving the bot to a new machine — stop the old one first.

## Common problems

| Symptom | Cause |
|---|---|
| No reply in a group | Privacy mode still on (step 4), or the bot was added before you changed it |
| `409 Conflict` in the logs | Another copy is running with the same token |
| Replies take a minute | A reasoning model with thinking on — set `OPENAI_REASONING_EFFORT=none` |
| "Semantic memory is OFF" | Your provider has no embeddings; see [docs/PROVIDERS.md](docs/PROVIDERS.md) |
| Nothing works, unclear why | `python doctor.py` |

## What it stores

One SQLite file (`TELEBOT_DB`, default `./telebot.db` when unset in a plain
checkout). It holds group messages, their embeddings, member names and polls.
Delete the file to reset the bot's memory entirely.

Only group messages **not** addressed to the bot are logged. Direct messages
are not stored.
