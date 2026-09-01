"""
AI-Powered Telebot — main.py
=============================
An intelligent Telegram group assistant that works with any of several AI
providers (Gemini, OpenAI, Claude) — pick yours with the AI_PROVIDER env var.
See providers/ for the adapter interface and README.md for setup.
"""

import os
import logging
import httpx
import asyncio
import io
import json
import uuid
from datetime import datetime, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from store import Store
from dotenv import load_dotenv

from providers import get_provider

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Local SQLite store. Default lives under the systemd StateDirectory; override
# with TELEBOT_DB when running the bot outside the unit.
TELEBOT_DB         = os.getenv("TELEBOT_DB", "/var/lib/telebot/telebot.db")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY")
ALPHA_VANTAGE_KEY  = os.getenv("ALPHA_VANTAGE_KEY")
AI_PROVIDER_NAME   = os.getenv("AI_PROVIDER", "gemini")
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL")  # e.g. "base"/"small" — enables local voice STT when set

if not TELEGRAM_BOT_TOKEN:
    logger.critical("MISSING TELEGRAM_BOT_TOKEN — check the secrets dir / .env.")

# ── CLIENT INIT ───────────────────────────────────────────────────────────────
store = Store(TELEBOT_DB)
logger.info(f"Store: {TELEBOT_DB}")
ai_provider = get_provider(AI_PROVIDER_NAME)
logger.info(f"AI provider: {ai_provider.name} (embeddings supported: {ai_provider.supports_embeddings})")

# In-memory session store (wiped on container restart — acceptable for group chat)
chat_sessions: dict = {}

# Lazily-loaded local faster-whisper model (only when WHISPER_MODEL is set).
_whisper_model = None


# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
def get_system_prompt() -> str:
    return (
        "Be really concise — don't reply in a long fashion unless absolutely necessary. "
        "Reply like we have known each other for years: light-hearted, real, like talking to a friend. "
        "If the user asks you to show or send an image, reply ONLY with a direct public image URL string."
    )


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

async def get_network_time() -> datetime:
    """Fetches real atomic time from Apple's HTTP Date header (falls back to
    the system clock on failure) — used everywhere a timestamp is needed."""
    try:
        async with httpx.AsyncClient() as c:
            res = await c.head("https://www.apple.com", timeout=2.0)
            date_str = res.headers.get("Date", "")
        utc_dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
        return utc_dt.replace(tzinfo=pytz.utc).astimezone(pytz.timezone("Asia/Singapore"))
    except Exception as e:
        logger.warning(f"NTP fetch failed — falling back to system clock: {e}")
        return datetime.now(pytz.timezone("Asia/Singapore"))


async def search_the_live_web(query: str) -> str:
    """Tavily web search with include_answer=True for a direct synthesised
    answer — essential for sports scores and live event results."""
    if not TAVILY_API_KEY:
        return "Tavily API key not configured."
    try:
        async with httpx.AsyncClient() as c:
            res = await c.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 5,
                },
                timeout=8.0,
            )
            data = res.json()

        answer  = data.get("answer", "")
        results = data.get("results", [])

        if not answer and not results:
            return "No live data found for this query."

        output = ""
        if answer:
            output += f"Direct Answer: {answer}\n\n"
        for idx, item in enumerate(results[:3], 1):
            output += f"[{idx}] {item.get('title', '')}\n{item.get('content', '')}\n\n"
        return output.strip()

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return "Web search failed."


async def fetch_live_financial_data(asset_type: str, symbol: str) -> str:
    """Stock/forex/commodity quotes via Alpha Vantage. Rate-limit notes are
    surfaced as readable messages instead of silently returning empty data."""
    if not ALPHA_VANTAGE_KEY:
        return "Alpha Vantage API key not configured."

    symbol = symbol.strip().upper()

    try:
        async with httpx.AsyncClient() as c:

            # ── STOCKS ───────────────────────────────────────────────────────
            if asset_type == "STOCK":
                res   = await c.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": ALPHA_VANTAGE_KEY},
                    timeout=6.0,
                )
                data  = res.json()
                quote = data.get("Global Quote", {})

                if not quote or not quote.get("05. price"):
                    note = data.get("Note") or data.get("Information", "")
                    if note:
                        return f"Alpha Vantage rate limit hit: {note}"
                    return f"No quote returned for '{symbol}'. Verify the ticker symbol."

                return (
                    f"📈 {quote['01. symbol']}\n"
                    f"Price   : ${quote['05. price']}\n"
                    f"High/Low: ${quote['03. high']} / ${quote['04. low']}\n"
                    f"Prev Close: ${quote['08. previous close']}\n"
                    f"Change  : {quote['10. change percent']}"
                )

            # ── FOREX ────────────────────────────────────────────────────────
            elif asset_type == "FOREX":
                from_c = symbol[:3]
                to_c   = symbol[3:] if len(symbol) > 3 else "USD"
                res    = await c.get(
                    "https://www.alphavantage.co/query",
                    params={
                        "function": "CURRENCY_EXCHANGE_RATE",
                        "from_currency": from_c,
                        "to_currency": to_c,
                        "apikey": ALPHA_VANTAGE_KEY,
                    },
                    timeout=6.0,
                )
                rate = res.json().get("Realtime Currency Exchange Rate", {})
                if not rate:
                    return f"No exchange rate data returned for {from_c}/{to_c}."
                return (
                    f"💱 {rate['1. From_Currency Code']}/{rate['3. To_Currency Code']}\n"
                    f"Rate: {rate['5. Exchange Rate']}\n"
                    f"Bid: {rate['8. Bid Price']} | Ask: {rate['9. Ask Price']}"
                )

            # ── COMMODITIES ──────────────────────────────────────────────────
            elif asset_type == "COMMODITY":
                COMM_MAP = {
                    "GOLD": "GOLD", "SILVER": "SILVER",
                    "OIL": "CRUDE_OIL", "CRUDE": "CRUDE_OIL", "WTI": "CRUDE_OIL",
                    "BRENT": "BRENT", "GAS": "NATURAL_GAS",
                    "COPPER": "COPPER", "WHEAT": "WHEAT",
                }
                function = COMM_MAP.get(symbol, symbol)
                res      = await c.get(
                    "https://www.alphavantage.co/query",
                    params={"function": function, "apikey": ALPHA_VANTAGE_KEY},
                    timeout=6.0,
                )
                points = res.json().get("data", [])
                if not points:
                    return f"No commodity data returned for {function}."
                latest = points[0]
                return f"🛢 {function}\nDate: {latest['date']}\nSpot: ${latest['value']} USD"

    except Exception as e:
        logger.error(f"Financial API error [{asset_type}/{symbol}]: {e}")
        return f"Market data fetch failed: {e}"

    return f"Unrecognised asset type: {asset_type}"


# ── VOICE TRANSCRIPTION ───────────────────────────────────────────────────────
def _load_whisper():
    """Load (once) and return the local faster-whisper model, or None if the
    feature is off (WHISPER_MODEL unset) or the package isn't installed. Called
    inside a worker thread — the load is CPU-heavy and must not block the loop.
    ponytail: global-cached, no load lock; two simultaneous first-ever voice
    notes could double-load the model — add a lock only if that ever shows up."""
    global _whisper_model
    if not WHISPER_MODEL_NAME:
        return None
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
            logger.info(f"Local Whisper loaded: {WHISPER_MODEL_NAME} (cpu/int8)")
        except Exception as e:
            logger.error(f"faster-whisper unavailable ({e}) — `pip install faster-whisper` or unset WHISPER_MODEL.")
            _whisper_model = False  # sentinel: tried and failed, don't retry every message
    return _whisper_model or None


async def transcribe_voice(audio: bytes, mime_type: str) -> str | None:
    """Transcribe a voice note to text. Prefers a local faster-whisper model —
    STT is independent of the chat LLM, so this works even when chat runs on a
    local Ollama with no audio endpoint — and falls back to the active provider's
    transcribe() when that provider is audio-capable (cloud gemini/openai)."""
    def _local():
        model = _load_whisper()
        if model is None:
            return None
        segments, _ = model.transcribe(io.BytesIO(audio))
        return " ".join(seg.text for seg in segments).strip()

    try:
        text = await asyncio.to_thread(_local)
        if text:
            return text
    except Exception as e:
        logger.error(f"Local Whisper transcription failed: {e}")

    if ai_provider.supports_audio:
        return await ai_provider.transcribe(audio, mime_type)
    return None


# ── LIVE MESSAGE REACTIONS ────────────────────────────────────────────────────
# ponytail: free keyword→emoji heuristic so the bot "reacts" in the group like a
# person, with no LLM call per message. Upgrade to a model-picked emoji only if
# these start feeling flat. Emojis are restricted to Telegram's allowed free
# reaction set — any other emoji makes set_reaction 400.
_REACTION_RULES = [
    (("lol", "lmao", "lmfao", "haha", "😂", "🤣", "hilarious", "joke"), "🤣"),
    (("congrats", "congratulations", "we won", "winner", "got the job", "promoted", "passed", "nailed it"), "🎉"),
    (("🔥", "goated", "let's go", "lets go", "insane", "banger", "cracked", "beast"), "🔥"),
    (("love", "❤", "😍", "gorgeous", "beautiful", "adorable", "cute"), "❤"),
    (("rip", "😢", "so sad", "heartbroken", "that sucks", "gutted", "condolences"), "😢"),
    (("wow", "no way", "unbelievable", "shocked", "can't believe", "🤯"), "🤯"),
    (("thank", "🙏", "appreciate", "grateful"), "🙏"),
    (("100", "💯", "facts", "exactly", "true that", "well said"), "💯"),
]


def pick_reaction(text: str) -> str | None:
    """Pick one Telegram reaction emoji for a message, or None to stay silent.
    Only reacts on a clear match, so most messages get nothing — that sparsity
    is what keeps it feeling alive instead of spammy."""
    low = text.lower()
    for triggers, emoji in _REACTION_RULES:
        if any(t in low for t in triggers):
            return emoji
    return None


async def route_message(user_text: str) -> dict:
    """Routes a message to ONE action and extracts its parameters in a single
    JSON call, using whichever AI provider is configured. This replaces the old
    brittle keyword ladder (`"remind" in text`, `"poll:" in text`, ...) so the
    bot understands intent from natural phrasing, not exact keywords.

    ponytail: one JSON router, not per-provider native tool-calling — it reuses
    generate_json (which every provider already implements) so it's vendor-
    agnostic for free. Upgrade to native function-calling only if you ever need
    multi-tool chains, mid-conversation tool use, or streamed tool output.
    Few-shot examples keep small/lite models reliable."""
    routing_prompt = f"""You are a strict JSON intent router for a Telegram assistant bot.
Read the user message and return EXACTLY ONE JSON object from the actions below.

━━ LIVE DATA ━━
STOCK — a stock / share price / company equity value. → {{"type": "STOCK", "symbol": "<TICKER>"}}
  "how much is lululemon" → {{"type": "STOCK", "symbol": "LULU"}}
  "what is tesla trading at" → {{"type": "STOCK", "symbol": "TSLA"}}
FOREX — a currency exchange rate. → {{"type": "FOREX", "symbol": "<FROM><TO>"}}
  "SGD to USD" → {{"type": "FOREX", "symbol": "SGDUSD"}}
COMMODITY — gold/silver/oil/gas/copper/wheat. → {{"type": "COMMODITY", "symbol": "<ASSET>"}}
  "crude oil" → {{"type": "COMMODITY", "symbol": "OIL"}}
WEB_SEARCH — live scores, news, weather, crypto prices, ongoing events. → {{"type": "WEB_SEARCH"}}
  "who won the F1 race today" → {{"type": "WEB_SEARCH"}}
  "current price of ethereum" → {{"type": "WEB_SEARCH"}}

━━ GROUP FEATURES ━━
REMIND — user wants to be reminded / alerted about something later. → {{"type": "REMIND"}}
  "ping me to call mum after lunch" → {{"type": "REMIND"}}
  "don't let me forget the standup at 9" → {{"type": "REMIND"}}
POLL — user wants to start a vote/poll. Extract the question and 2+ options. → {{"type": "POLL", "question": "<q>", "options": ["<a>", "<b>"]}}
  "poll: pizza | sushi | tacos" → {{"type": "POLL", "question": "Pick one", "options": ["pizza", "sushi", "tacos"]}}
  "let's vote on lunch, thai or korean" → {{"type": "POLL", "question": "Lunch?", "options": ["Thai", "Korean"]}}
SUMMARIZE — user wants a recap of recent group chat. → {{"type": "SUMMARIZE"}}
  "what did i miss" → {{"type": "SUMMARIZE"}}
  "catch me up" → {{"type": "SUMMARIZE"}}
ROAST — user asks for your read/opinion on a specific group member. Extract their name. → {{"type": "ROAST", "target": "<name>"}}
  "what do you think of dave" → {{"type": "ROAST", "target": "dave"}}
  "give me your honest take on sarah" → {{"type": "ROAST", "target": "sarah"}}
MEMORY — user is trying to recall a past conversation/topic. Extract the search query. → {{"type": "MEMORY", "query": "<topic>"}}
  "where did we land on the venue" → {{"type": "MEMORY", "query": "the venue"}}
  "what was that restaurant someone mentioned" → {{"type": "MEMORY", "query": "restaurant"}}

━━ FALLBACK ━━
CHAT — general conversation, jokes, opinions, explanations, math, history, greetings. → {{"type": "CHAT"}}
  "how are you" → {{"type": "CHAT"}}
  "explain quantum computing" → {{"type": "CHAT"}}

HARD RULES (never violate):
- Live/recent sports scores, news, weather, crypto prices → WEB_SEARCH (never CHAT).
- Company names map to tickers: lululemon=LULU, apple=AAPL, google=GOOGL, meta=META.
- A POLL needs 2+ options; if you can't find them, use CHAT instead.
- Return ONLY the raw JSON object — no markdown, no explanation, no extra text.

User message: "{user_text}"
"""

    try:
        route = await ai_provider.generate_json(routing_prompt)
        logger.info(f"[ROUTER] '{user_text}' → {route}")
        return route
    except Exception as e:
        logger.error(f"Message routing error: {e} — defaulting to CHAT")
        return {"type": "CHAT"}


# ═════════════════════════════════════════════════════════════════════════════
# JOB QUEUE CALLBACKS
# ═════════════════════════════════════════════════════════════════════════════

async def execute_dynamic_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Fires when a scheduled reminder job triggers."""
    d = context.job.data
    await context.bot.send_message(
        chat_id=d["chat_id"],
        text=f"🔔 **REMINDER FOR {d['user'].upper()}:**\n\n> {d['reminder_text']}",
        parse_mode="Markdown",
    )


async def expire_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Fires when a poll window closes. Tallies votes, determines winner,
    edits the original message, then deletes the DB record."""
    d = context.job.data
    poll_id, chat_id, message_id = d["poll_id"], d["chat_id"], d["message_id"]
    try:
        poll = await asyncio.to_thread(lambda: store.get_poll(poll_id))
        if not poll:
            return

        options       = poll["options"]
        result_text   = f"📊 **POLL CLOSED: {poll['question']}**\n\nFinal Standings:\n"
        max_v, winner = -1, "Nobody voted!"

        for opt, count in options.items():
            result_text += f"▪️ {opt}: **{count} votes**\n"
            if count > max_v and count > 0:
                max_v, winner = count, opt
            elif count == max_v and count > 0:
                winner = f"Tie between {winner} and {opt}!"

        result_text += f"\n🏆 **Winner:** {winner}"

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=result_text, parse_mode="Markdown",
        )
        await asyncio.to_thread(lambda: store.delete_poll(poll_id))
    except Exception as e:
        logger.error(f"Poll expiry error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# POLL CALLBACK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def handle_poll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline keyboard button presses on live polls."""
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    if ":" not in query.data:
        return

    poll_id, opt_idx_str = query.data.split(":", 1)

    try:
        poll = await asyncio.to_thread(lambda: store.get_poll(poll_id))
        if not poll:
            await query.answer("This poll has already ended!", show_alert=True)
            return

        opts  = poll["options"]
        votes = poll["votes"]
        keys  = list(opts.keys())
        sel   = keys[int(opt_idx_str)]

        # Handle vote changes
        if user_id in votes:
            prior = votes[user_id]
            if prior == sel:
                return  # Same vote — no-op
            opts[prior] = max(0, opts[prior] - 1)

        votes[user_id] = sel
        opts[sel]     += 1

        await asyncio.to_thread(lambda: store.update_poll(poll_id, opts, votes))

        updated_kb = [
            [InlineKeyboardButton(f"{k} ({opts[k]})", callback_data=f"{poll_id}:{i}")]
            for i, k in enumerate(keys)
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(updated_kb))

    except Exception as e:
        logger.error(f"Poll callback error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN MESSAGE HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Core handler — logs context, routes intent, and delivers a response."""
    if not update.effective_chat or not update.message:
        return

    chat_id   = update.effective_chat.id
    chat_type = update.message.chat.type
    user_name = update.message.from_user.first_name if update.message.from_user else "Someone"
    user_text = update.message.text or update.message.caption or ""

    bot_info     = await context.bot.get_me()
    bot_username = f"@{bot_info.username}"
    is_mentioned = bot_username.lower() in user_text.lower() or chat_type == "private"

    # ── IMAGE HANDLING ────────────────────────────────────────────────────────
    image_bytes = None
    if update.message.photo:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        buf  = bytearray()
        await file.download_to_memory(buf)
        image_bytes = bytes(buf)
        if chat_type in ["group", "supergroup"]:
            is_mentioned = True

    # ── VOICE HANDLING ─────────────────────────────────────────────────────────
    # Transcribe the voice note to text, then let it flow through the exact same
    # routing/chat pipeline as a typed message. Mirrors the image path: a voice
    # note in a group is always treated as directed (you can't @mention by voice).
    if update.message.voice and not user_text:
        if WHISPER_MODEL_NAME or ai_provider.supports_audio:
            try:
                vfile = await context.bot.get_file(update.message.voice.file_id)
                vbuf  = bytearray()
                await vfile.download_to_memory(vbuf)
                mime  = update.message.voice.mime_type or "audio/ogg"
                user_text = await transcribe_voice(bytes(vbuf), mime) or ""
                logger.info(f"[VOICE] transcribed: {user_text!r}")
            except Exception as e:
                logger.error(f"Voice transcription error: {e}")
            if chat_type in ["group", "supergroup"]:
                is_mentioned = True
        elif chat_type == "private":
            # Graceful degradation, same shape as the semantic-memory fallback.
            await context.bot.send_message(
                chat_id=chat_id,
                text=("🎙️ Voice notes need a local Whisper (set WHISPER_MODEL) or an "
                      "audio-capable provider (gemini/openai). Or just type it out."),
            )
            return

    if not user_text and not image_bytes:
        return

    # ── LIVE REACTIONS (every group message, mentioned or not) ────────────────
    if chat_type in ("group", "supergroup") and user_text:
        emoji = pick_reaction(user_text)
        if emoji:
            try:
                await update.message.set_reaction(reaction=emoji)
            except Exception as e:
                logger.debug(f"Reaction failed (non-fatal): {e}")

    # ── BACKGROUND LOGGING (non-directed group messages only) ─────────────────
    if chat_type in ["group", "supergroup"] and not is_mentioned and user_text:
        try:
            await asyncio.to_thread(
                lambda: store.log_message(chat_id, user_name, user_text)
            )
            # Semantic memory requires an embedding-capable provider (Gemini/OpenAI).
            # Claude has no embeddings API, so this step is skipped automatically.
            if ai_provider.supports_embeddings:
                vec = await ai_provider.embed(f"{user_name}: {user_text}")
                await asyncio.to_thread(
                    lambda: store.add_embedding(chat_id, user_name, user_text, vec)
                )
        except Exception as e:
            logger.error(f"Background log/embed error: {e}")

    if not is_mentioned:
        return

    # ── SESSION INIT ──────────────────────────────────────────────────────────
    if chat_id not in chat_sessions:
        chat_sessions[chat_id] = ai_provider.create_chat(get_system_prompt())

    chat           = chat_sessions[chat_id]
    cleaned_text   = user_text.replace(bot_username, "").strip()
    prompt_payload: str | None = None

    # Images are vision requests — skip routing and hand the caption to the model.
    # Everything else goes through ONE JSON router that picks the action AND
    # extracts its params (see route_message), replacing the old keyword ladder.
    action: str = "CHAT"
    route: dict = {}
    if not image_bytes and user_text:
        route  = await route_message(user_text)
        action = route.get("type", "CHAT")

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 1 — GROUP SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    if action == "SUMMARIZE":
        try:
            records = await asyncio.to_thread(
                lambda: store.recent_messages(chat_id, limit=500)
            )
            history = "\n".join(f"{r['sender']}: {r['message']}" for r in records) or "No logs yet."
        except Exception as e:
            logger.error(f"Summary DB fetch error: {e}")
            history = f"DB error: {e}"

        prompt_payload = (
            f"Summary request. Here are the recent group chat logs:\n"
            f"### LOGS ###\n{history}\n### END ###\n\n"
            f"Give a short, light-hearted recap of what went down."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 2 — PEER PERSONALITY ROAST
    # ══════════════════════════════════════════════════════════════════════════
    elif action == "ROAST":
        target = (route.get("target") or "").strip()
        try:
            records = await asyncio.to_thread(
                lambda: store.messages_by_sender(chat_id, target, limit=200)
            )
            if records:
                history = "\n".join(f"- {r['message']}" for r in records)
                prompt_payload = (
                    f"Personality assessment of '{target}' based purely on their messages:\n"
                    f"{history}\n\nBe funny, punchy, and authentic — like roasting a close friend. Keep it short!"
                )
            else:
                prompt_payload = f"Tell the user you found no messages from '{target}' in the database yet."
        except Exception as e:
            logger.error(f"Peer roast DB error: {e}")
            prompt_payload = "Tell user the database threw an error while profiling."

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 3 — NATURAL LANGUAGE REMINDERS
    # ══════════════════════════════════════════════════════════════════════════
    elif action == "REMIND":
        live_dt  = await get_network_time()
        time_ctx = live_dt.strftime("%A, %d %B %Y, %I:%M %p SGT")

        parse_prompt = (
            f"You are a JSON extraction utility. Current time: {time_ctx}.\n"
            f"User said: '{user_text}'\n\n"
            f"Return ONLY this JSON — no markdown, no extra text:\n"
            f'{{"target_timestamp": "YYYY-MM-DD HH:MM:SS", "task": "description of what to remind them to do"}}'
        )

        try:
            raw    = await ai_provider.generate_text(parse_prompt)
            clean  = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(clean)

            sg_tz  = pytz.timezone("Asia/Singapore")
            naive  = datetime.strptime(parsed["target_timestamp"], "%Y-%m-%d %H:%M:%S")
            target = sg_tz.localize(naive)

            if target <= datetime.now(sg_tz):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="That time is already in the past! Pick a future moment. ⏳",
                )
                return

            context.application.job_queue.run_once(
                execute_dynamic_reminder,
                when=target,
                data={"chat_id": chat_id, "reminder_text": parsed["task"], "user": user_name},
            )
            readable = target.strftime("%A, %d %B at %I:%M %p SGT")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Done! I'll remind you to **{parsed['task']}** on {readable}. 🫡",
                parse_mode="Markdown",
            )
            return
        except Exception as e:
            logger.error(f"Reminder parse/schedule error: {e}")
            prompt_payload = "Tell user the reminder scheduler hit a parsing snag."

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 4 — SEMANTIC LONG-TERM MEMORY SEARCH
    # ══════════════════════════════════════════════════════════════════════════
    elif action == "MEMORY":
        if not ai_provider.supports_embeddings:
            prompt_payload = (
                f"Tell the user semantic memory search isn't available because the "
                f"current AI provider ('{ai_provider.name}') doesn't support embeddings — "
                f"suggest they switch AI_PROVIDER to gemini or openai for this feature, "
                f"or try asking for a 'summary' instead."
            )
        else:
            try:
                vec = await ai_provider.embed(route.get("query") or user_text)

                matches = await asyncio.to_thread(
                    lambda: store.match_embeddings(
                        chat_id, vec, threshold=0.3, count=5
                    )
                )
                if matches:
                    mem = "\n".join(f"- {m['sender']}: {m['message']}" for m in matches)
                    prompt_payload = (
                        f"Semantic memory search results:\n{mem}\n\n"
                        f"Answer the user's question concisely based on these records."
                    )
                else:
                    prompt_payload = "Tell user nothing matched in the vector memory index for that topic."
            except Exception as e:
                logger.error(f"Vector search error: {e}")
                prompt_payload = "Tell user the semantic search pipeline failed."

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 5 — INTERACTIVE LIVE POLLS
    # ══════════════════════════════════════════════════════════════════════════
    elif action == "POLL":
        question = (route.get("question") or "Pick one").strip()
        options  = [str(o).strip() for o in (route.get("options") or []) if str(o).strip()]

        if len(options) < 2:
            await context.bot.send_message(
                chat_id=chat_id,
                text="I need at least two options for a poll. Try: `poll: Question | Option 1 | Option 2`",
                parse_mode="Markdown",
            )
            return

        try:
            poll_id  = str(uuid.uuid4())[:8]
            opts_map = {opt: 0 for opt in options}
            sg_tz    = pytz.timezone("Asia/Singapore")
            expires  = datetime.now(sg_tz) + timedelta(minutes=5)

            await asyncio.to_thread(
                lambda: store.create_poll(
                    poll_id, chat_id, question, opts_map, expires.isoformat()
                )
            )

            buttons = [
                [InlineKeyboardButton(f"{opt} (0)", callback_data=f"{poll_id}:{i}")]
                for i, opt in enumerate(options)
            ]
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=f"📊 **LIVE POLL**\n\n> {question}\n\n_Closes in 5 minutes!_",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )
            context.application.job_queue.run_once(
                expire_poll_job,
                when=expires,
                data={"poll_id": poll_id, "chat_id": chat_id, "message_id": sent.message_id},
            )
            return
        except Exception as e:
            logger.error(f"Poll creation error: {e}")
            prompt_payload = "Tell user the poll creation failed."

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 6 & 7 — LIVE FINANCIAL DATA + WEB SEARCH
    # ══════════════════════════════════════════════════════════════════════════
    elif action in ("STOCK", "FOREX", "COMMODITY"):
        symbol = route.get("symbol", "")
        status = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📊 Pulling live data for **{symbol}**...",
            parse_mode="Markdown",
        )
        market_data = await fetch_live_financial_data(action, symbol)
        await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
        prompt_payload = (
            f"Live market data:\n{market_data}\n\n"
            f"Analyze this like a knowledgeable friend. Keep it punchy and contextual."
        )

    elif action == "WEB_SEARCH":
        status = await context.bot.send_message(chat_id=chat_id, text="🔍 Scanning the live wire...")
        web_data = await search_the_live_web(user_text)
        await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
        prompt_payload = (
            f"Live web data:\n{web_data}\n\n"
            f"Answer the user's question concisely based on this. Don't pad it out."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # REGULAR CHAT (default) — also handles image captions
    # ══════════════════════════════════════════════════════════════════════════
    else:
        prompt_payload = cleaned_text

    # ── FETCH LIVE TIMESTAMP ───────────────────────────────────────────────────
    live_dt   = await get_network_time()
    ts_string = live_dt.strftime("%A, %d %B %Y, %I:%M %p SGT")

    # ── ASSEMBLE PAYLOAD ───────────────────────────────────────────────────────
    message_text = f"[Live Time: {ts_string}]\nUser: {prompt_payload or 'Analyze this image.'}"
    image_arg = (image_bytes, "image/jpeg") if image_bytes else None

    # ── SEND TO THE AI PROVIDER — RETRY ON TRANSIENT FAILURE ──────────────────
    for attempt in range(3):
        try:
            bot_response = await chat.send(message_text, image=image_arg)

            is_image_url = bot_response.startswith(("http://", "https://")) and any(
                bot_response.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
            )

            if is_image_url:
                await context.bot.send_photo(chat_id=chat_id, photo=bot_response, caption="📸")
            else:
                await context.bot.send_message(chat_id=chat_id, text=bot_response)
            return

        except Exception as e:
            logger.error(f"AI provider response error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            await context.bot.send_message(
                chat_id=chat_id, text="AI servers are swamped or something went sideways. Try again?"
            )
            return


# ═════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resets the chat session and re-syncs the clock."""
    if update.effective_chat:
        chat_sessions.pop(update.effective_chat.id, None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Session wiped and clocks synced. What's good? 🫡",
        )


# ═════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═════════════════════════════════════════════════════════════════════════════

async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_poll_callback))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VOICE) & ~filters.COMMAND, handle_message))

    logger.info("Bot online — all modules active.")

    async with app:
        await app.start()
        # drop_pending_updates prevents the bot from replaying stale messages
        # after a container restart (e.g. on Railway).
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shut down cleanly.")
