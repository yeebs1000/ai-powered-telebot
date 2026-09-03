"""doctor.py: every failure path must name the fix, and exit non-zero."""
import os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

def run(**env):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("AI_", "OPENAI_", "EMBED_", "TELEGRAM_", "TELEBOT_",
                              "DEEPSEEK_", "OPENROUTER_", "GROQ_", "TOGETHER_",
                              "GEMINI_", "ANTHROPIC_", "VAULT_"))}
    e.update({k: str(v) for k, v in env.items()})
    e.setdefault("TELEBOT_DB", os.path.join(tempfile.mkdtemp(), "d.db"))
    p = subprocess.run([PY, "doctor.py", "--offline"], cwd=ROOT, env=e,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr

# nothing configured -> fails, and names both missing things
rc, out = run()
assert rc == 1, out
assert "TELEGRAM_BOT_TOKEN is not set" in out and "@BotFather" in out
assert "AI_PROVIDER is not set" in out and "openrouter" in out
print("  unconfigured -> exit 1, names the token and the provider list")

# a malformed token is caught before any network call
rc, out = run(TELEGRAM_BOT_TOKEN="not-a-token", AI_PROVIDER="local",
              OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m")
assert rc == 1 and "does not look like a bot token" in out, out
print("  malformed token caught offline")

# unknown provider lists the supported ones
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="chatgpt5")
assert rc == 1 and "not supported" in out and "deepseek" in out, out
print("  unknown provider -> lists supported names")

# router with no model: the error must say what to set, with an example
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="openrouter", OPENROUTER_API_KEY="k")
assert rc == 1 and "AI_MODEL" in out and "anthropic/" in out, out
print("  router without AI_MODEL -> exit 1 with a worked example")

# router WITH a model: usable, but warns that memory is off
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="openrouter",
              OPENROUTER_API_KEY="k", AI_MODEL="anthropic/claude-sonnet-4.5")
assert rc == 0, out
assert "Semantic memory is OFF" in out and "EMBED_BASE_URL" in out, out
print("  router with model -> exit 0, warns memory is off and how to fix")

# adding an embed route clears that warning
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="openrouter",
              OPENROUTER_API_KEY="k", AI_MODEL="x/y",
              EMBED_BASE_URL="http://127.0.0.1:1/v1", EMBED_MODEL="nomic-embed-text")
assert rc == 0 and "Semantic memory is OFF" not in out, out
print("  embed route configured -> warning clears")

# reasoning model without the flag is warned about, not failed
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="local",
              OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="qwen3.5:9b")
assert rc == 0 and "reasoning model" in out and "OPENAI_REASONING_EFFORT" in out, out
print("  reasoning model -> warning with the measured reason")

# unwritable database is a hard failure
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="local",
              OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
              TELEBOT_DB="/proc/nope/x.db")
assert rc == 1 and "Cannot write the database" in out, out
print("  unwritable DB -> exit 1")

# a healthy offline config passes
rc, out = run(TELEGRAM_BOT_TOKEN="1:a", AI_PROVIDER="local",
              OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
              OPENAI_REASONING_EFFORT="none",
              EMBED_BASE_URL="http://127.0.0.1:1/v1", EMBED_MODEL="e")
assert rc == 0 and "Ready to run" in out, out
print("  healthy config -> exit 0")

# no secret is ever echoed
rc, out = run(TELEGRAM_BOT_TOKEN="123456:SUPERSECRETVALUE", AI_PROVIDER="local",
              OPENAI_BASE_URL="http://127.0.0.1:1/v1", AI_MODEL="m",
              OPENROUTER_API_KEY="sk-or-SECRETKEY")
assert "SUPERSECRETVALUE" not in out and "SECRETKEY" not in out, out
assert "123456" in out, "the bot id is public and useful, keep it"
print("  secrets never echoed (bot id is, deliberately)")
print("ALL DOCTOR TESTS PASSED")
