"""Provider registry — picks an AIProvider implementation based on AI_PROVIDER.

Three providers have their own adapter because their APIs differ: `gemini`,
`openai` and `claude`. Everything else in PRESETS speaks the OpenAI wire
format, so it reuses the OpenAI adapter with a different base URL, key and
default model. Adding a service that is OpenAI-compatible is one line here,
not a new file.
"""

import os

from .base import AIProvider, ChatSession

_instances: dict[str, AIProvider] = {}

# OpenAI-compatible services. `embeddings` records whether the service serves
# an embeddings endpoint itself; where it does not, semantic memory needs
# EMBED_BASE_URL pointed somewhere that does (a local Ollama is the usual
# answer). It is not a limitation of this bot -- routers simply proxy chat.
PRESETS: dict[str, dict] = {
    "openai": {
        "base_url": None,
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "embeddings": True,
        "docs": "https://platform.openai.com/api-keys",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "embeddings": False,
        "docs": "https://platform.deepseek.com/api_keys",
    },
    "openrouter": {
        # One key, hundreds of models from every vendor. There is no sensible
        # default model: the whole point is that you choose one, so AI_MODEL is
        # required and the error below says so plainly.
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": None,
        "embeddings": False,
        "docs": "https://openrouter.ai/keys",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "default_model": None,
        "embeddings": False,
        "docs": "https://console.groq.com/keys",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY",
        "default_model": None,
        "embeddings": True,
        "docs": "https://api.together.xyz/settings/api-keys",
    },
    "local": {
        # Ollama, LM Studio, vLLM, llama.cpp — anything OpenAI-shaped.
        # base_url comes from OPENAI_BASE_URL; the key is ignored by most.
        "base_url": None,
        "key_env": "OPENAI_API_KEY",
        "default_model": None,
        "embeddings": False,
        "docs": "https://ollama.com",
    },
}

NATIVE = ("gemini", "claude")
SUPPORTED = tuple(sorted(set(PRESETS) | set(NATIVE)))


def _model_for(role: str, default: str | None) -> str | None:
    """Per-role model with a shared fallback.

    AI_MODEL_ROUTER lets the strict-JSON intent call run on something small and
    fast while AI_MODEL_CHAT answers people. On a router that split is most of
    the cost difference; locally it is most of the latency.
    """
    return os.getenv(f"AI_MODEL_{role.upper()}") or os.getenv("AI_MODEL") or default


def get_provider(name: str) -> AIProvider:
    """Return a cached AIProvider for `name`.

    Raises ValueError with an actionable message if the name is unknown or a
    required key or model is missing.
    """
    name = (name or "gemini").lower()
    if name in _instances:
        return _instances[name]

    if name == "gemini":
        from .gemini_provider import GeminiProvider
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("AI_PROVIDER=gemini requires GEMINI_API_KEY to be set.")
        model = _model_for("chat", None)
        provider = GeminiProvider(api_key, **({"model": model} if model else {}))

    elif name == "claude":
        from .claude_provider import ClaudeProvider
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("AI_PROVIDER=claude requires ANTHROPIC_API_KEY to be set.")
        model = _model_for("chat", None)
        provider = ClaudeProvider(api_key, **({"model": model} if model else {}))

    elif name in PRESETS:
        from .openai_provider import OpenAIProvider
        preset = PRESETS[name]

        # OPENAI_BASE_URL always wins, so a preset can be pointed at a proxy or
        # a self-hosted gateway without editing this table.
        base_url = os.getenv("OPENAI_BASE_URL") or preset["base_url"]
        api_key = os.getenv(preset["key_env"]) or os.getenv("OPENAI_API_KEY")

        # Only a self-hosted endpoint may run without a key. A hosted preset
        # has a base_url of its own, so testing base_url alone would let a
        # missing key through as the dummy below and fail with a 401 at the
        # first message instead of at startup.
        self_hosted = name == "local" or bool(os.getenv("OPENAI_BASE_URL"))
        if not api_key and not self_hosted:
            raise ValueError(
                f"AI_PROVIDER={name} requires {preset['key_env']} "
                f"(get one at {preset['docs']}), or OPENAI_BASE_URL pointing at "
                f"an OpenAI-compatible server you host."
            )
        if self_hosted and not base_url:
            raise ValueError(
                "AI_PROVIDER=local requires OPENAI_BASE_URL — e.g. "
                "http://localhost:11434/v1 for Ollama."
            )
        if not api_key:
            api_key = "local"   # self-hosted servers ignore it; the SDK wants one

        model = _model_for("chat", preset["default_model"])
        if not model:
            raise ValueError(
                f"AI_PROVIDER={name} has no default model — set AI_MODEL to the "
                f"model you want (see {preset['docs']}). Example for openrouter: "
                f"AI_MODEL=anthropic/claude-sonnet-4.5"
            )

        kwargs = {
            "model": model,
            "router_model": _model_for("router", model),
            "label": name,
        }
        if base_url:
            kwargs["base_url"] = base_url

        # Reasoning models spend most of their output on discarded thinking.
        # Set OPENAI_REASONING_EFFORT=none for a reasoning model; unset keeps
        # cloud-OpenAI behaviour exactly.
        reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT") or None
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        # Embeddings may live somewhere else entirely — see PRESETS.
        embed_model = os.getenv("EMBED_MODEL") or os.getenv("OPENAI_EMBED_MODEL") or None
        embed_base_url = os.getenv("EMBED_BASE_URL") or None
        if embed_model:
            kwargs["embed_model"] = embed_model
        if embed_base_url:
            kwargs["embed_base_url"] = embed_base_url
            kwargs["embed_api_key"] = os.getenv("EMBED_API_KEY") or "local"

        provider = OpenAIProvider(api_key, **kwargs)

    else:
        raise ValueError(
            f"Unknown AI_PROVIDER '{name}'. Supported: {', '.join(SUPPORTED)}."
        )

    _instances[name] = provider
    return provider


__all__ = ["AIProvider", "ChatSession", "get_provider", "PRESETS", "SUPPORTED"]
