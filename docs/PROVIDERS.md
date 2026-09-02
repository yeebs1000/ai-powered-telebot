# Choosing a provider and assigning models

[English](PROVIDERS.md) · [简体中文](PROVIDERS.zh-CN.md)

The bot never talks to a vendor SDK directly. Every AI call goes through an
adapter in [`providers/`](../providers/), so switching provider is an
environment variable, not a rewrite.

## The short version

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5
```

That is a working configuration. Everything below is refinement.

## Supported providers

| `AI_PROVIDER` | Key | Default model | Embeddings |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | ✅ built in |
| `gemini` | `GEMINI_API_KEY` | provider default | ✅ built in |
| `claude` | `ANTHROPIC_API_KEY` | provider default | ❌ no embeddings API |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | ❌ chat only |
| `openrouter` | `OPENROUTER_API_KEY` | **you must set `AI_MODEL`** | ❌ chat only |
| `groq` | `GROQ_API_KEY` | **you must set `AI_MODEL`** | ❌ chat only |
| `together` | `TOGETHER_API_KEY` | **you must set `AI_MODEL`** | ✅ |
| `local` | none needed | **you must set `AI_MODEL`** | via `EMBED_*` |

`openai`, `gemini` and `claude` have their own adapters because their APIs
differ. Everything else speaks the OpenAI wire format and reuses the OpenAI
adapter with a different base URL — which is also why adding another
OpenAI-compatible service is one line in `providers/__init__.py`.

### Routers have no default model, on purpose

The point of a router is that *you* choose the model, so there is no sensible
default and the bot refuses to invent one:

```
AI_PROVIDER=openrouter has no default model — set AI_MODEL to the model you
want. Example for openrouter: AI_MODEL=anthropic/claude-sonnet-4.5
```

Model names are the provider's own. On OpenRouter they are namespaced
(`anthropic/claude-sonnet-4.5`, `deepseek/deepseek-chat`,
`meta-llama/llama-3.3-70b-instruct`); on Groq and DeepSeek they are bare
(`deepseek-chat`).

## Assigning different models to different jobs

The bot makes **two** model calls per handled message:

1. **the router** — one strict-JSON call that decides what the message is
   (a question needing live data, a reminder, a poll, a catch-up, a reply);
2. **the reply** — the actual answer.

They want different things. The router wants speed and reliable JSON; the reply
wants quality. Split them:

```bash
AI_MODEL=anthropic/claude-sonnet-4.5              # everything, unless overridden
AI_MODEL_ROUTER=meta-llama/llama-3.1-8b-instruct  # the JSON call only
```

`AI_MODEL` is the base default for every role. `AI_MODEL_<ROLE>` overrides that
role alone, so `AI_MODEL_CHAT` does not drag the router with it.

| Variable | Applies to |
|---|---|
| `AI_MODEL` | every role, unless overridden |
| `AI_MODEL_CHAT` | the reply |
| `AI_MODEL_ROUTER` | the strict-JSON intent call |

On a router this split is most of the bill — the JSON call is short and
frequent, and a small model does it just as well. Locally it is most of the
latency.

### Router models must return valid JSON

The intent router asks for a strict JSON object. Models below roughly 7B are
unreliable at this, and a failed parse falls back to plain chat — so features
stop firing **silently**. If reminders and polls stop working, suspect the
router model first.

## Keeping semantic memory when your provider has no embeddings

Semantic memory ("where did we land on that restaurant") needs an embeddings
endpoint. Routers proxy chat completions and generally do not serve one, so
memory would quietly switch off.

Point embeddings somewhere else — a local Ollama is the usual answer, and it
costs nothing:

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5

EMBED_BASE_URL=http://localhost:11434/v1   # embeddings only
EMBED_MODEL=nomic-embed-text               # 768-dim
```

Chat goes to the router; embeddings go to your machine. Two clients, one bot.

| Variable | Purpose |
|---|---|
| `EMBED_BASE_URL` | OpenAI-compatible embeddings endpoint |
| `EMBED_MODEL` | Embedding model name |
| `EMBED_API_KEY` | Only if that endpoint needs one |

Leave them unset and the bot uses the main provider's embeddings if it has
them, or turns memory off and says so.

## Running fully local

```bash
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL=nomic-embed-text
OPENAI_REASONING_EFFORT=none
```

No keys, no bills, nothing leaves the machine.

### Turn thinking off on reasoning models

A reasoning model spends most of its output on thinking that is then discarded.
Measured on `qwen3.5-agent:32k`, one reply:

| | tokens | time |
|---|---|---|
| thinking on | 1686 | 118s |
| thinking off | 68 | 5.8s |

Two calls per message makes that the entire latency budget. `OPENAI_REASONING_EFFORT=none`
is the fix. Note that against Ollama's OpenAI-compatible endpoint only
`reasoning_effort` works — `"think": false` and
`chat_template_kwargs: {"enable_thinking": false}` are both ignored.

### Check where your server is actually listening

`localhost:11434` is right only if Ollama is bound there. If you set
`OLLAMA_HOST` (for example to a Docker bridge so containers can reach it),
loopback has nothing listening and the bot will fail to connect. Confirm with
`ss -lntp | grep 11434`.

## Cost and privacy at a glance

| Setup | Cost | What leaves your machine |
|---|---|---|
| Local (Ollama) | none | nothing |
| DeepSeek | very low | messages sent to the model |
| Router (OpenRouter, Groq, Together) | varies by model | messages sent to the model, via the router |
| OpenAI / Gemini / Claude | varies | messages sent to the model |

Chat history is always stored locally in SQLite regardless of provider.
