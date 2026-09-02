"""OpenAI adapter — chat, structured JSON, and embeddings.

text-embedding-3-small/large support a `dimensions` parameter that truncates
the output vector, so we request 768 dims to stay compatible with the
vector(768) column used by the Gemini provider's schema.
"""

import asyncio
import base64
import json

from openai import OpenAI

from .base import AIProvider, ChatSession

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMENSIONS = 768  # the width the local store's vectors are written at


class OpenAIChatSession(ChatSession):
    def __init__(self, client: OpenAI, model: str, system_prompt: str,
                 reasoning_effort: str | None = None):
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._history: list[dict] = [{"role": "system", "content": system_prompt}]

    async def send(self, text: str, image: tuple[bytes, str] | None = None) -> str:
        content: list[dict] = [{"type": "text", "text": text}]
        if image:
            data, mime_type = image
            b64 = base64.b64encode(data).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            })

        self._history.append({"role": "user", "content": content})

        kwargs = {"model": self._model, "messages": self._history, "temperature": 0.7}
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        response = await asyncio.to_thread(
            lambda: self._client.chat.completions.create(**kwargs)
        )
        reply = response.choices[0].message.content.strip()
        self._history.append({"role": "assistant", "content": reply})
        return reply


class OpenAIProvider(AIProvider):
    name = "openai"
    supports_embeddings = True
    supports_audio = True

    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str | None = None, embed_model: str | None = None,
                 reasoning_effort: str | None = None):
        # base_url lets this same adapter drive any OpenAI-compatible server —
        # Ollama, LM Studio, vLLM, llama.cpp — so a local model needs no new
        # provider file. base_url=None keeps the default (cloud OpenAI).
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._local = bool(base_url)
        self._embed_model = embed_model or EMBED_MODEL
        # Reasoning models burn most of their output on thinking that is then
        # discarded. On a local box that is the whole latency budget: a 127-char
        # reply cost 1686 tokens and ~118s, versus 68 tokens and ~6s with
        # reasoning off. Passed through to every completion call.
        self._reasoning_effort = reasoning_effort
        # A local server usually has no audio endpoint, and only does embeddings
        # if you point it at an embedding model — reflect that per-instance
        # instead of assuming cloud OpenAI's full feature set, so semantic memory
        # and voice degrade gracefully rather than erroring.
        self.supports_audio = not self._local
        self.supports_embeddings = (not self._local) or bool(embed_model)

    def create_chat(self, system_prompt: str) -> ChatSession:
        return OpenAIChatSession(self._client, self._model, system_prompt,
                                 self._reasoning_effort)

    async def generate_text(self, prompt: str) -> str:
        kwargs = {"model": self._model,
                  "messages": [{"role": "user", "content": prompt}]}
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        response = await asyncio.to_thread(
            lambda: self._client.chat.completions.create(**kwargs)
        )
        return response.choices[0].message.content.strip()

    async def generate_json(self, prompt: str) -> dict:
        kwargs = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        response = await asyncio.to_thread(
            lambda: self._client.chat.completions.create(**kwargs)
        )
        return json.loads(response.choices[0].message.content.strip())

    async def embed(self, text: str) -> list[float] | None:
        def _create():
            kwargs = {"model": self._embed_model, "input": text}
            # `dimensions` truncation is a cloud-OpenAI feature; local servers
            # (e.g. Ollama's nomic-embed-text) are natively 768-dim, so omit it.
            if not self._local:
                kwargs["dimensions"] = EMBED_DIMENSIONS
            return self._client.embeddings.create(**kwargs)

        response = await asyncio.to_thread(_create)
        return response.data[0].embedding

    async def transcribe(self, audio: bytes, mime_type: str) -> str | None:
        # Telegram voice notes are OGG/Opus; whisper-1 accepts them directly.
        # The tuple (filename, bytes) lets the SDK infer format from the extension.
        response = await asyncio.to_thread(
            lambda: self._client.audio.transcriptions.create(
                model="whisper-1",
                file=("voice.ogg", audio),
            )
        )
        return response.text.strip()
