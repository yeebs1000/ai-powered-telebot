"""Provider registry: presets, per-role models, split embedding route, errors."""
import os, sys
sys.path.insert(0, '/opt/telebot')
import providers
from providers import get_provider, PRESETS, SUPPORTED

def fresh(**env):
    providers._instances.clear()
    for k in list(os.environ):
        if k.startswith(("AI_MODEL", "EMBED_", "OPENAI_", "DEEPSEEK_", "OPENROUTER_",
                         "GROQ_", "TOGETHER_", "GEMINI_", "ANTHROPIC_")):
            del os.environ[k]
    os.environ.update(env)

def err(**env):
    fresh(**env)
    try:
        get_provider(env.get("_name", "openrouter")); return None
    except ValueError as e:
        return str(e)

# every preset resolves with just its own key (where a default model exists)
for name, preset in PRESETS.items():
    if not preset["default_model"]:
        continue
    fresh(**{preset["key_env"]: "k"})
    p = get_provider(name)
    assert p._model == preset["default_model"], (name, p._model)
    assert p.name == name, (name, p.name)
print(f"  {len([p for p in PRESETS.values() if p['default_model']])} presets resolve from their own key")

# a router with no default model must say so, not guess
fresh(OPENROUTER_API_KEY="k")
try:
    get_provider("openrouter"); raise SystemExit("should have raised")
except ValueError as e:
    assert "AI_MODEL" in str(e) and "openrouter" in str(e), e
print("  openrouter without AI_MODEL -> actionable error, no guessed model")

fresh(OPENROUTER_API_KEY="k", AI_MODEL="anthropic/claude-sonnet-4.5")
p = get_provider("openrouter")
assert p._model == "anthropic/claude-sonnet-4.5"
assert str(p._client.base_url).startswith("https://openrouter.ai")
print("  openrouter + AI_MODEL ->", p._model)

# per-role models: router split from chat
fresh(OPENROUTER_API_KEY="k", AI_MODEL="anthropic/claude-sonnet-4.5",
      AI_MODEL_ROUTER="meta-llama/llama-3.1-8b-instruct")
p = get_provider("openrouter")
assert p._model == "anthropic/claude-sonnet-4.5"
assert p._router_model == "meta-llama/llama-3.1-8b-instruct"
print("  AI_MODEL_ROUTER splits the JSON call from the chat model")

# AI_MODEL is the base default for every role; AI_MODEL_<ROLE> overrides that
# role alone. So a chat override must NOT drag the router with it.
fresh(DEEPSEEK_API_KEY="k", AI_MODEL="deepseek-chat", AI_MODEL_CHAT="deepseek-reasoner")
p = get_provider("deepseek")
assert p._model == "deepseek-reasoner", p._model
assert p._router_model == "deepseek-chat", p._router_model
print("  AI_MODEL_CHAT overrides chat only; router stays on AI_MODEL")

# with no AI_MODEL at all, the router inherits whatever chat resolved to
fresh(DEEPSEEK_API_KEY="k", AI_MODEL_CHAT="deepseek-reasoner")
p = get_provider("deepseek")
assert p._model == "deepseek-reasoner" and p._router_model == "deepseek-reasoner"
print("  no AI_MODEL: router inherits the chat model")

# routers have no embeddings -> memory off, unless an embed route is given
fresh(OPENROUTER_API_KEY="k", AI_MODEL="x/y")
assert get_provider("openrouter").supports_embeddings is False
fresh(OPENROUTER_API_KEY="k", AI_MODEL="x/y",
      EMBED_BASE_URL="http://127.0.0.1:11434/v1", EMBED_MODEL="nomic-embed-text")
p = get_provider("openrouter")
assert p.supports_embeddings is True
assert p._embed_client is not p._client, "embeddings must use the separate client"
assert str(p._embed_client.base_url).startswith("http://127.0.0.1:11434")
assert str(p._client.base_url).startswith("https://openrouter.ai")
print("  chat via router + embeddings via local endpoint, two clients")

# cloud openai keeps one client and native embeddings
fresh(OPENAI_API_KEY="k")
p = get_provider("openai")
assert p.supports_embeddings is True and p._embed_client is p._client
print("  cloud openai unchanged: one client, native embeddings")

# unknown provider lists what is supported
fresh()
try:
    get_provider("gpt5-turbo-ultra"); raise SystemExit("should have raised")
except ValueError as e:
    assert "Supported:" in str(e) and "deepseek" in str(e), e
print("  unknown provider lists supported names")

# missing key names the env var and where to get one
fresh()
try:
    get_provider("deepseek"); raise SystemExit("should have raised")
except ValueError as e:
    assert "DEEPSEEK_API_KEY" in str(e) and "deepseek.com" in str(e), e
print("  missing key names the variable and the signup URL")

# OPENAI_BASE_URL always wins, so a preset can be proxied
fresh(DEEPSEEK_API_KEY="k", OPENAI_BASE_URL="http://proxy.local/v1")
assert str(get_provider("deepseek")._client.base_url).startswith("http://proxy.local")
print("  OPENAI_BASE_URL overrides a preset's base URL")
print("ALL PROVIDER TESTS PASSED")
