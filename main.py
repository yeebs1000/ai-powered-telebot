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
from collections import Counter
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

SGT = pytz.timezone("Asia/Singapore")


_bot_info = None


async def get_bot_info(bot):
    """The bot's own identity, fetched once.

    This was an API round trip on every single message, including the ones the
    bot only overhears. The answer changes when someone renames the bot in
    BotFather -- a restart picks that up, which is the right trade for taking a
    network call out of the hot path.
    """
    global _bot_info
    if _bot_info is None:
        _bot_info = await bot.get_me()
        logger.info(f"Bot identity cached: @{_bot_info.username}")
    return _bot_info


def now_sgt() -> datetime:
    """Current local time.

    This used to HEAD https://www.apple.com and parse the Date header, which
    was labelled NTP but is not: it is a network round trip on the reply path,
    it fails whenever the internet does, and it is second-resolution at best.
    The host already keeps time properly — read the clock.
    """
    return datetime.now(SGT)


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


# A catch-up used to paste up to 500 raw lines into the model and ask for a
# recap. That is a context dump, not a summary: it is most of the latency, it
# pushes a 9B past the point where it tracks who said what, and the answer
# drifts into paraphrase. Bound the input and ask for structure instead.
CATCHUP_MAX_MESSAGES = 120
CATCHUP_MAX_CHARS = 6000


def build_catchup_prompt(user_name: str, records: list[dict], since: str) -> str:
    """Deterministic facts first, then a bounded transcript, then bullets.

    The counts and the speaker list are computed here, not asked of the model:
    they are arithmetic, they are always right, and a model that has to count
    is a model spending attention on the wrong thing.
    """
    speakers = Counter(r["sender"] for r in records)
    total = len(records)

    # Keep the most recent messages: when a window has to be trimmed, what
    # happened last is what the person is walking back into.
    kept = records[-CATCHUP_MAX_MESSAGES:]
    while kept and sum(len(r["message"]) + len(r["sender"]) + 2 for r in kept) > CATCHUP_MAX_CHARS:
        kept.pop(0)

    transcript = "\n".join(f"{r['sender']}: {r['message']}" for r in kept)
    who = ", ".join(f"{name} ({n})" for name, n in speakers.most_common())

    trimmed = ""
    if len(kept) < total:
        # Say so rather than quietly summarising a fraction as if it were all.
        trimmed = (f"\n(Only the most recent {len(kept)} of {total} messages are "
                   f"shown; say so if the earlier part matters.)")

    logger.info(f"[CATCHUP] {user_name}: {total} messages from {len(speakers)} "
                f"people since {since}; {len(kept)} sent to the model")

    return (
        f"{user_name} has been away. While they were gone: {total} messages "
        f"from {len(speakers)} people — {who}.\n"
        f"### LOGS ###\n{transcript}\n### END ###{trimmed}\n\n"
        f"Write the catch-up as at most 8 short bullets, in this order:\n"
        f"- decisions the group actually made (who, what)\n"
        f"- questions still open\n"
        f"- anything that involves {user_name} directly\n"
        f"Skip small talk, reactions and banter. Do not invent a decision that "
        f"was only discussed. If nothing was decided, say that in one line. "
        f"No preamble, no closing sentence."
    )


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
    """Fires when a scheduled reminder job triggers.

    The row is marked fired only after the message is actually sent, so a crash
    between the two leaves it pending and it is retried on the next start --
    late is recoverable, silently dropped is not.
    """
    d = context.job.data
    text = f"🔔 **REMINDER FOR {d['user'].upper()}:**\n\n> {d['reminder_text']}"
    if d.get("late_since"):
        # Arriving hours after the fact with no explanation is its own bug.
        text += f"\n\n_(late — this was due {d['late_since']}; I was restarting.)_"
    await context.bot.send_message(chat_id=d["chat_id"], text=text, parse_mode="Markdown")
    if d.get("reminder_id"):
        await asyncio.to_thread(lambda: store.mark_reminder_fired(d["reminder_id"]))


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
    bot_info     = await get_bot_info(context.bot)
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
                prompt_payload = build_catchup_prompt(user_name, records, since)
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
    # PEER PERSONALITY ROAST — REMOVED 2026-09-03
    # ══════════════════════════════════════════════════════════════════════════
    # This built a "funny, punchy, authentic" character read of a named member
    # from 200 of their own messages and posted it to the group. The model has
    # no idea which of those messages are jokes, which are raw, or what is
    # currently going on in that person's life -- and the output went to
    # everyone, about someone who never agreed to be profiled.
    #
    # The asymmetry is what makes it not worth keeping: it is mildly amusing
    # when it lands and unrecoverable when it does not. One bad roast and the
    # bot is out of the group, and nothing else in this repo earns that risk
    # back.
    #
    # If it ever returns it is DM-only and requires that person's explicit
    # opt-in, which does not exist yet. The router no longer emits ROAST, so
    # "what do you think of dave" falls through to ordinary chat.
    #
    # store.messages_by_member() and profiles.member_block() are deliberately
    # kept: identity resolution and style profiles are still used elsewhere.

    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE 3 — NATURAL LANGUAGE REMINDERS
    # ══════════════════════════════════════════════════════════════════════════
    elif action == "REMIND":
        live_dt  = now_sgt()
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

            # Written before the job is scheduled and before the user is told
            # "done": the promise must exist on disk from the moment it is made.
            reminder_id = await asyncio.to_thread(
                lambda: store.create_reminder(
                    chat_id, user_id, user_name, parsed["task"],
                    target.astimezone(pytz.utc).isoformat(),
                )
            )
            context.application.job_queue.run_once(
                execute_dynamic_reminder,
                when=target,
                data={"chat_id": chat_id, "reminder_text": parsed["task"],
                      "user": user_name, "reminder_id": reminder_id},
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
            await asyncio.to_thread(
                lambda: store.set_poll_message(poll_id, sent.message_id)
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
    live_dt   = now_sgt()
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

async def rehydrate(app) -> tuple[int, int]:
    """Put the job queue back after a restart.

    Everything the bot promised lives in SQLite; the schedule that fires it
    does not. Without this, persistence is just a table nobody reads: a
    reminder written at 09:00 and a restart at 09:30 still never arrives.

    Anything already due is fired immediately rather than dropped. Late and
    labelled beats silent.
    """
    now = datetime.now(pytz.utc)
    reminders = await asyncio.to_thread(store.pending_reminders)
    for r in reminders:
        try:
            due = datetime.fromisoformat(r["due_at"])
        except ValueError:
            logger.error(f"[REHYDRATE] reminder {r['id']} has unparseable due_at; skipping")
            continue
        data = {"chat_id": r["chat_id"], "reminder_text": r["text"],
                "user": r["user_name"], "reminder_id": r["id"]}
        if due <= now:
            data["late_since"] = due.astimezone(
                pytz.timezone("Asia/Singapore")).strftime("%a %d %b %I:%M %p SGT")
            app.job_queue.run_once(execute_dynamic_reminder, when=0, data=data)
        else:
            app.job_queue.run_once(execute_dynamic_reminder, when=due, data=data)

    polls = await asyncio.to_thread(store.pending_polls)
    restored_polls = 0
    for poll in polls:
        if not poll.get("message_id"):
            # Created before message ids were recorded: there is no message to
            # edit, so closing it would be a no-op that leaves a dead row.
            await asyncio.to_thread(lambda pid=poll["poll_id"]: store.delete_poll(pid))
            logger.info(f"[REHYDRATE] dropped poll {poll['poll_id']} (no message id)")
            continue
        try:
            expires = datetime.fromisoformat(poll["expires_at"])
        except ValueError:
            await asyncio.to_thread(lambda pid=poll["poll_id"]: store.delete_poll(pid))
            continue
        data = {"poll_id": poll["poll_id"], "chat_id": poll["chat_id"],
                "message_id": poll["message_id"]}
        app.job_queue.run_once(expire_poll_job,
                               when=0 if expires <= now else expires, data=data)
        restored_polls += 1

    if reminders or restored_polls:
        logger.info(f"[REHYDRATE] {len(reminders)} reminder(s), "
                    f"{restored_polls} poll(s) restored from the store")
    return len(reminders), restored_polls


async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_poll_callback))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VOICE) & ~filters.COMMAND, handle_message))

    logger.info("Bot online — all modules active.")

    async with app:
        await app.start()
        # After start(), so the job queue is running and accepts jobs.
        await rehydrate(app)
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
