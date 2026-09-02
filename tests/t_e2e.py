"""End-to-end: real local embeddings through the provider into the store."""
import asyncio, sys, tempfile, os
sys.path.insert(0, '/opt/telebot')
from providers import get_provider
from store import Store

s = Store(os.path.join(tempfile.mkdtemp(), "e2e.db"))
p = get_provider("openai")
print("provider:", p.name, "| embeddings:", p.supports_embeddings)
assert p.supports_embeddings, "embeddings should be on with OPENAI_EMBED_MODEL set"

async def main():
    corpus = [
        ("alice", "we should get sushi for lunch tomorrow"),
        ("bob",   "the deployment pipeline broke again last night"),
        ("carol", "my cat knocked over a glass of water"),
    ]
    for sender, msg in corpus:
        vec = await p.embed(f"{sender}: {msg}")
        assert len(vec) == 768, len(vec)
        s.add_embedding(-100, sender, msg, vec)
    print("embedded", len(corpus), "messages at 768 dims")

    for query, expect in [("what food did we talk about?", "sushi"),
                          ("any problems with the build?", "pipeline"),
                          ("tell me about pets", "cat")]:
        q = await p.embed(query)
        hits = s.match_embeddings(-100, q, threshold=0.3, count=3)
        top = hits[0]["message"] if hits else "(no match)"
        ok = "PASS" if expect in top else "MISS"
        print(f"  [{ok}] {query!r}\n         -> {top!r} ({hits[0]['similarity']:.3f})" if hits
              else f"  [MISS] {query!r} -> no match")

asyncio.run(main())
print("E2E MEMORY PATH OK")

# Needs a reachable model endpoint; skipped by tests/run.sh without --all.
REQUIRES_MODEL = True
