FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/ ./data/
COPY pyproject.toml README.md ./

# The firms, the scanners and the example bots. Without config/ the image
# builds, starts, serves a page, and then `trade init` says "no firm config at
# config/firm_config.yaml" — a village with no villagers, from an image that
# looked fine. bots/ matters for the same reason: config names
# bots/example_scanner.py, and a scanner whose file is not in the image is a
# scanner that reports itself missing on every tick.
COPY config/ ./config/
COPY bots/ ./bots/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

# One image, two jobs, chosen by MVV_ROLE — see scripts/serve.sh. The web
# service serves pages; the worker ticks the village. Neither is the default
# by accident: with MVV_ROLE unset this serves, which is what a plain
# `docker run` should do.
CMD ["./scripts/serve.sh"]
