"""Prove the live provider config (as the unit runs it) is fast and non-empty."""
import asyncio, sys, time
sys.path.insert(0, '/opt/telebot')
from providers import get_provider
p = get_provider("openai")

async def main():
    t=time.time(); r = await p.generate_json(
        'Return exactly one JSON object: {"type":"CHAT"}. Message: "hows everything bro"')
    print(f"  router  : {time.time()-t:5.1f}s -> {r}")
    chat = p.create_chat("You are a friendly group chat bot. Keep replies short.")
    t=time.time(); reply = await chat.send("hows everything bro, sean here missed u")
    print(f"  chat    : {time.time()-t:5.1f}s -> {reply[:90]!r}")
    assert reply.strip(), "empty reply"
    t=time.time(); reply2 = await chat.send("what did i just say?")
    print(f"  follow  : {time.time()-t:5.1f}s -> {reply2[:90]!r}")
asyncio.run(main())
print("SPEED TEST OK")
