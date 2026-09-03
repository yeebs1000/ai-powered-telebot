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
import re
import uuid
from datetime import datetime, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from store import Store
from vault import VaultReference
from reactions import pick_reaction, ReactionLimiter, SemanticReactor
from profiles import ProfileBuilder
from dotenv import load_dotenv

from providers import get_provider

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# httpx logs every request URL at INFO, and python-telegram-bot puts the bot
# token IN the URL path — so INFO-level httpx writes the token into the journal
# on every single API call, forever. WARNING keeps the failures, drops the URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Local SQLite store. Default lives under the systemd StateDirectory; override
# with TELEBOT_DB when running the bot outside the unit.
# Relative by default so a fresh clone runs without root or a writable
# /var/lib. The systemd unit sets an absolute path for a real deployment.
TELEBOT_DB         = os.getenv("TELEBOT_DB") or "telebot.db"
# One vault folder, mounted read-only by the unit. Unset = feature off. The
# bot cannot see any other part of the vault, which is the point: scope is the
# safeguard, not trust in per-note classification.
VAULT_REF_ROOT     = os.getenv("VAULT_REF_ROOT") or None
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY")
AI_PROVIDER_NAME   = os.getenv("AI_PROVIDER", "gemini")
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL")  # e.g. "base"/"small" — enables local voice STT when set

if not TELEGRAM_BOT_TOKEN:
    logger.critical("MISSING TELEGRAM_BOT_TOKEN — check the secrets dir / .env.")

# ── CLIENT INIT ───────────────────────────────────────────────────────────────
store = Store(TELEBOT_DB)
logger.info(f"Store: {TELEBOT_DB}")
reaction_limiter = ReactionLimiter()
# Learned from the log, not fine-tuned: recomputed on a TTL so it improves
# with every message the group sends.
profiles = ProfileBuilder(store)
vault_ref = VaultReference(VAULT_REF_ROOT)
logger.info(f"Vault reference: {VAULT_REF_ROOT or 'disabled'}"
            f"{'' if vault_ref.enabled else ' (unreadable — running without it)'}")
ai_provider = get_provider(AI_PROVIDER_NAME)
logger.info(f"AI provider: {ai_provider.name} (embeddings supported: {ai_provider.supports_embeddings})")

# Reactions by meaning, not spelling. Falls back to the keyword table when no
# embedding model is configured or a call fails.
semantic_reactor = SemanticReactor(
    ai_provider.embed if ai_provider.supports_embeddings else None,
    cache_path=os.path.join(os.getenv("STATE_DIRECTORY", "/var/lib/telebot"),
                            "reaction_centroids.json"),
)

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
SUMMARIZE — user wants a recap of the recent conversation generally. → {{"type": "SUMMARIZE"}}
  "summarise the chat" → {{"type": "SUMMARIZE"}}
  "what has everyone been talking about" → {{"type": "SUMMARIZE"}}
CATCHUP — user asks what THEY personally missed while away. → {{"type": "CATCHUP"}}
  "what did i miss" → {{"type": "CATCHUP"}}
  "catch me up" → {{"type": "CATCHUP"}}
  "anything i missed while i was out" → {{"type": "CATCHUP"}}
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

# Phrases that introduce someone. Deliberately explicit: tagging the bot in a
# reply is ordinary conversation, so binding only fires on a naming sentence.
_NAMING_PATTERNS = [
    re.compile(r"\b(?:this|that)\s+(?:is|was)\s+(?:called\s+)?([A-Za-z][\w'\-]{1,30})", re.I),
    re.compile(r"\b(?:he|she|they|it)\s*(?:'s|s|\s+is|\s+are)\s+(?:called\s+)?([A-Za-z][\w'\-]{1,30})", re.I),
    re.compile(r"\b(?:call|calls)\s+(?:him|her|them)?\s*([A-Za-z][\w'\-]{1,30})", re.I),
    re.compile(r"\b(?:name|names)\s+(?:is|are)\s+([A-Za-z][\w'\-]{1,30})", re.I),
    re.compile(r"\bmeet\s+([A-Za-z][\w'\-]{1,30})", re.I),
]

# Words that survive the patterns above but are never somebody's name.
_NOT_A_NAME = {
    "a", "an", "the", "my", "our", "your", "his", "her", "their", "friend",
    "mate", "bro", "guy", "girl", "person", "member", "everyone", "someone",
    "me", "you", "us", "them", "him", "it", "not", "just", "actually", "really",
}


def _extract_offered_name(text: str) -> str | None:
    for pattern in _NAMING_PATTERNS:
        m = pattern.search(text)
        if m:
            name = m.group(1).strip(" '-")
            if name and name.lower() not in _NOT_A_NAME:
                return name
    return None


async def try_bind_identity(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            chat_id: int, user_text: str, bot_username: str) -> str | None:
    """Bind a spoken name to a real member when the bot is tagged alongside them.

    The referent is whoever the message points at: the author of the message
    being replied to, or a mentioned user. Returns a line to send back, or None
    when this is not a naming message and should flow on to normal handling.
    """
    referent = None

    reply = update.message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        referent = reply.from_user

    # A mention entity carries the user object directly when they have no
    # @username; otherwise resolve the @handle against known members.
    mentioned_name = None
    if referent is None:
        for ent in (update.message.entities or []):
            if ent.type == "text_mention" and ent.user and not ent.user.is_bot:
                referent = ent.user
                break
            if ent.type == "mention":
                handle = user_text[ent.offset:ent.offset + ent.length]
                if handle.lower() != bot_username.lower():
                    mentioned_name = handle
        if referent is None and mentioned_name:
            match = await asyncio.to_thread(
                lambda: store.resolve_member(chat_id, mentioned_name)
            )
            if match:
                referent = type("M", (), {"id": match["user_id"],
                                          "first_name": match["first_name"]})()

    if referent is None:
        return None

    # Strip the bot's own handle so "@bot this is Sean" doesn't offer "bot".
    cleaned = re.sub(re.escape(bot_username), " ", user_text, flags=re.I)
    name = _extract_offered_name(cleaned)
    if not name:
        return None

    existing = await asyncio.to_thread(lambda: store.resolve_member(chat_id, name))
    if existing and existing["user_id"] != referent.id:
        return (f"I already know {name} as someone else in here "
                f"({existing['first_name']}). Not overwriting that — "
                f"pick a different name if they're two different people.")

    added = await asyncio.to_thread(
        lambda: store.add_alias(chat_id, referent.id, name)
    )
    if added:
        logger.info(f"[IDENTITY] bound '{name}' → user {referent.id}")
        return f"Got it — that's {name}. I'll remember."
    # add_alias returns False for an unknown member as well as a duplicate.
    known = await asyncio.to_thread(lambda: store.resolve_member(chat_id, name))
    if known:
        return f"Already had them down as {name}."
    return (f"I can't record that yet — I've not seen {name} post in here, "
            f"so there's no member to attach the name to.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Core handler — logs context, routes intent, and delivers a response."""
    if not update.effective_chat or not update.message:
        return

    chat_id   = update.effective_chat.id
    chat_type = update.message.chat.type
    from_user = update.message.from_user
    user_name = from_user.first_name if from_user else "Someone"
    user_id   = from_user.id if from_user else None
    user_text = update.message.text or update.message.caption or ""

    # Telegram's user_id is the only stable handle on a person: display names
    # change and some cannot be typed at all. Refreshed on every message so a
    # rename is picked up without losing the names the group has taught.
    if user_id and chat_type in ("group", "supergroup"):
        await asyncio.to_thread(
            lambda: store.upsert_member(chat_id, user_id, user_name,
                                        from_user.username if from_user else None)
        )

    bot_info     = await context.bot.get_me()
    bot_username = f"@{bot_info.username}"
    is_mentioned = bot_username.lower() in user_text.lower() or chat_type == "private"

    # ── IDENTITY BINDING ──────────────────────────────────────────────────────
    # "tag me together with them" — when the bot is mentioned in a message that
    # also points at a specific person (a reply, or a mention entity), any name
    # offered in the text is bound to that person's user_id. This is the escape
    # hatch for members no name can reach: an emoji display name, or someone the
    # group calls something entirely unlike their Telegram name.
    if is_mentioned and chat_type in ("group", "supergroup") and user_text:
        bound = await try_bind_identity(update, context, chat_id, user_text, bot_username)
        if bound:
            await context.bot.send_message(chat_id=chat_id, text=bound)
            return

    # ── IMAGE HANDLING ────────────────────────────────────────────────────────
    image_bytes = None
    if update.message.photo:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        buf  = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()
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
                vbuf  = io.BytesIO()
                await vfile.download_to_memory(vbuf)
                mime  = update.message.voice.mime_type or "audio/ogg"
                user_text = await transcribe_voice(vbuf.getvalue(), mime) or ""
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
    # The embedding is computed at most once per message and used twice: for
    # the reaction here, and for semantic memory when the message is logged
    # below. Computing it twice would double the per-message cost for nothing.
    message_vec = None
    vec_attempted = False

    async def get_vec():
        nonlocal message_vec, vec_attempted
        if vec_attempted:
            return message_vec
        vec_attempted = True
        if ai_provider.supports_embeddings and user_text:
            try:
                message_vec = await ai_provider.embed(f"{user_name}: {user_text}")
            except Exception as e:
                logger.error(f"Embedding failed: {e}")
        return message_vec

    if chat_type in ("group", "supergroup") and user_text:
        emoji = None
        if ai_provider.supports_embeddings and semantic_reactor.worth_embedding(user_text):
            if await semantic_reactor.prepare():
                emoji = semantic_reactor.pick_from_vector(await get_vec())
        # Keywords remain the backstop: narrow, but never unavailable.
        if emoji is None:
            emoji = pick_reaction(user_text)
        if emoji and reaction_limiter.allow(chat_id):
            try:
                await update.message.set_reaction(reaction=emoji)
            except Exception as e:
                logger.debug(f"Reaction failed (non-fatal): {e}")

    # ── BACKGROUND LOGGING (non-directed group messages only) ─────────────────
    if chat_type in ["group", "supergroup"] and not is_mentioned and user_text:
        try:
            await asyncio.to_thread(
                lambda: store.log_message(chat_id, user_name, user_text, user_id)
            )
            # Semantic memory requires an embedding-capable provider (Gemini/OpenAI).
            # Claude has no embeddings API, so this step is skipped automatically.
            if ai_provider.supports_embeddings:
                vec = await get_vec()
                if vec:
                    await asyncio.to_thread(
                        lambda: store.add_embedding(chat_id, user_name, user_text, vec, user_id)
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
    if action == "CATCHUP":
        # Anchored to when this person last spoke, not a fixed window: the
        # point of "what did I miss" is that the answer differs per person.
        try:
            records, since = await asyncio.to_thread(
                lambda: store.messages_since_user_last(
                    chat_id, user_id, user_name, limit=500)
            )
            if since is None:
                prompt_payload = (
                    "Tell the user you have nothing to catch them up on because "
                    "you have not seen them post in here before — from now on you "
                    "will track it. One short line."
                )
            elif not records:
                prompt_payload = (
                    "Tell the user nothing has been said since their last message. "
                    "One short line."
                )
            else:
                speakers = len({r["sender"] for r in records})
                history = "\n".join(f"{r['sender']}: {r['message']}" for r in records)
                logger.info(f"[CATCHUP] {user_name}: {len(records)} messages "
                            f"from {speakers} people since {since}")
                prompt_payload = (
                    f"{user_name} has been away. Here is everything said in the "
                    f"group since their last message — {len(records)} messages "
                    f"from {speakers} people:\n"
                    f"### LOGS ###\n{history}\n### END ###\n\n"
                    f"Tell them what they missed: the substance, any decisions, "
                    f"and anything still unresolved that involves them. Skip small "
                    f"talk. Be brief."
                )
        except Exception as e:
            logger.error(f"Catchup DB error: {e}")
            prompt_payload = "Tell the user the catch-up lookup failed."

    elif action == "SUMMARIZE":
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
            member = await asyncio.to_thread(
                lambda: store.resolve_member(chat_id, target)
            )
            if member:
                records = await asyncio.to_thread(
                    lambda: store.messages_by_member(
                        chat_id, member["user_id"], member["first_name"], limit=200
                    )
                )
                known_as = member["first_name"] or target
                logger.info(f"[IDENTITY] '{target}' → {known_as} "
                            f"(id {member['user_id']}, {member['match']})")
            else:
                # Fall back to the old name search: history logged before
                # identity tracking has no user_id to resolve against.
                records = await asyncio.to_thread(
                    lambda: store.messages_by_sender(chat_id, target, limit=200)
                )
                known_as = target
                logger.info(f"[IDENTITY] '{target}' unresolved; name search "
                            f"returned {len(records)} rows")

            if records:
                history = "\n".join(f"- {r['message']}" for r in records)
                style = await asyncio.to_thread(
                    lambda: profiles.member_block(chat_id, known_as)
                )
                prompt_payload = (
                    f"Personality assessment of '{known_as}' based purely on their messages:\n"
                    f"{history}\n\n{style}\n\n"
                    f"Be funny, punchy, and authentic — like roasting a close friend. Keep it short!"
                )
            elif member:
                prompt_payload = (
                    f"Tell the user you know who {known_as} is but they haven't "
                    f"said anything you've logged yet."
                )
            else:
                prompt_payload = (
                    f"Tell the user you don't know who '{target}' is yet, and that "
                    f"they can teach you by tagging you in a reply to that person's "
                    f"message saying who they are — e.g. replying to them with "
                    f"\"{bot_username} this is {target}\". Keep it to one short line."
                )
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
    # FEATURE 6 — WEB SEARCH
    # ══════════════════════════════════════════════════════════════════════════
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
    # Reference notes are advisory background, looked up from what was actually
    # said. No match means nothing is added, so ordinary chatter costs nothing.
    reference = ""
    if vault_ref.enabled and prompt_payload:
        try:
            reference = await asyncio.to_thread(
                lambda: vault_ref.context_block(prompt_payload)
            )
            if reference:
                logger.info(f"[VAULT] {len(reference)} chars of reference attached")
        except Exception as e:
            # Reference material is a nicety; never fail a reply over it.
            logger.error(f"Vault reference lookup failed: {e}")

    # How this group talks, derived from its own history. Empty until there is
    # enough of it, so a new chat is simply unstyled rather than mis-styled.
    style_block = ""
    if chat_type in ("group", "supergroup"):
        try:
            style_block = await asyncio.to_thread(
                lambda: profiles.group_block(chat_id)
            )
        except Exception as e:
            logger.error(f"Profile lookup failed: {e}")

    message_text = (
        (f"{style_block}\n\n" if style_block else "")
        + (f"{reference}\n\n" if reference else "")
        + f"[Live Time: {ts_string}]\nUser: {prompt_payload or 'Analyze this image.'}"
    )
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
