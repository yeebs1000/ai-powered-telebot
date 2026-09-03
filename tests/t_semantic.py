"""Semantic reactions: meaning over spelling, plus the guards."""
import asyncio, sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers import get_provider
from reactions import SemanticReactor, pick_reaction

cache = os.path.join(tempfile.mkdtemp(), "c.json")
p = get_provider("openai")
r = SemanticReactor(p.embed, cache_path=cache)

async def main():
    assert await r.prepare() is True
    assert os.path.exists(cache), "centroids should be cached to disk"
    r2 = SemanticReactor(p.embed, cache_path=cache)
    assert await r2.prepare() is True and r2._centroids
    print("  centroids computed and reloaded from cache")

    # the case that made this necessary: keywords call a death a celebration
    grief = "my grandad passed away last night"
    assert pick_reaction(grief) == "🎉", "keyword baseline should be the bad one"
    assert await r.pick(grief) == "😢"
    print("  'passed away': keywords 🎉 -> semantic 😢")

    for text, want in [
        ("we lost in the final again", "😢"),
        ("you saved me hours, seriously", "🙏"),
    ]:
        got = await r.pick(text)
        assert got == want, f"{text!r} -> {got}, wanted {want}"
    print("  meaning matched with no keyword present")

    for text in ["what time is the meeting", "sending the file now",
                 "the japan trip is booked", "dyson fan looks decent"]:
        got = await r.pick(text)
        assert got is None, f"{text!r} should stay silent, got {got}"
    print("  ordinary chatter stays silent")

    # Celebration is no longer matched by vibe. Measured on 141 real messages
    # it fired on "Better than I thought" and "my bot is back alive rn"; people
    # say congratulations explicitly when they mean it, so the keyword table
    # owns that and the vector layer keeps out of it.
    assert await r.pick("i finally got the offer letter") is None
    assert pick_reaction("congrats bro!") == "🎉"
    print("  celebration needs explicit words, not a vibe match")

    # Conversational agreement was the single biggest source of noise.
    for chatter in ["sounds good to me", "yeah its a fan ah",
                    "my shopee one works the same"]:
        assert await r.pick(chatter) is None, chatter
    print("  conversational agreement no longer matches anything")

    # Questions are silent on this path too — it used to answer "Does it work
    # though?" with 🤝.
    assert await r.pick("does that actually work though?") is None
    print("  questions stay silent on the vector path")

    # short strings sit near everything in embedding space
    assert r.worth_embedding("ok") is False
    assert await r.pick("ok") is None
    assert r.worth_embedding("a much longer sentence here") is True
    print("  very short messages skipped before embedding")

    # a broken embed must degrade, not raise
    async def boom(_): raise RuntimeError("model down")
    bad = SemanticReactor(boom, cache_path=None)
    assert await bad.prepare() is False
    assert await bad.pick("my grandad passed away last night") is None
    assert bad.pick_from_vector([0.1, 0.2]) is None
    print("  embedding failure degrades to keywords, no crash")
asyncio.run(main())
print("ALL SEMANTIC REACTION TESTS PASSED")

# Needs a reachable model endpoint; skipped by tests/run.sh without --all.
REQUIRES_MODEL = True
