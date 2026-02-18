# ── Stage 1: build ───────────────────────────────────────────────
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev \
    libxml2-dev libxslt1-dev \
    zlib1g-dev libjpeg62-turbo-dev libfreetype-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY inkfeed/ ./inkfeed/

# Install app + sleepscreen extra (playwright) into a clean prefix.
RUN pip install --no-cache-dir --prefix=/install '.[sleepscreen]'

# ── Stage 2: runtime ────────────────────────────────────────────
FROM python:3.13-slim

# Runtime native libs only (no compilers).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 \
    zlib1g libjpeg62-turbo libfreetype6 \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Let Playwright download Chromium and its OS-level dependencies.
RUN playwright install --with-deps chromium

WORKDIR /app

# Default config location — users bind-mount their own config.toml.
COPY config.toml ./

ENTRYPOINT ["inkfeed"]
