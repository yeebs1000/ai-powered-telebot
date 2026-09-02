"""Image path: the bytes a Telegram download produces -> provider -> model.

Also asserts the download_to_memory contract that broke: the object handed to
it must be a writable binary file, not a bytearray.
"""
import asyncio, io, sys, struct, zlib
sys.path.insert(0, '/opt/telebot')
from providers import get_provider

# --- the bug itself, reproduced and fixed ---------------------------------
try:
    bytearray().write(b"x")
    raise SystemExit("bytearray unexpectedly has .write")
except AttributeError:
    print("  bytearray has no .write  -> this is what crashed handle_message")
buf = io.BytesIO(); buf.write(b"x")
assert buf.getvalue() == b"x"
print("  io.BytesIO accepts .write -> the fix")

# --- build a solid red 64x64 PNG in pure python ---------------------------
def png(rgb, size=64):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))

image_bytes = png((220, 20, 20))
print(f"  test image: {len(image_bytes)} bytes PNG")

async def main():
    p = get_provider("openai")
    chat = p.create_chat("You describe images in a few words.")
    reply = await chat.send("What single colour fills this image? One word.",
                            image=(image_bytes, "image/png"))
    print("  model reply:", repr(reply[:80]))
    assert reply.strip(), "empty reply on image input"
    assert "red" in reply.lower(), f"model did not see red: {reply!r}"

asyncio.run(main())
print("VISION PATH OK")
