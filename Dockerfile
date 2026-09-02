# Small, non-root, and it runs the same code as a bare checkout.
FROM python:3.11-slim

# Dependencies first so they cache across code changes.
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The bot writes only its database. Keeping it in a named directory means a
# volume mount is one line and survives image rebuilds.
ENV TELEBOT_DB=/data/telebot.db
RUN useradd --system --no-create-home telebot \
 && mkdir -p /data && chown telebot:telebot /data
USER telebot
VOLUME ["/data"]

# Long polling: no port is exposed, and none is needed.
# Fails fast on a broken configuration instead of idling silently.
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s \
  CMD python doctor.py --offline || exit 1

CMD ["python", "main.py"]
