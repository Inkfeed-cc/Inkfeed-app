# Inkfeed

Your daily news, distilled.

Inkfeed pulls content from multiple news sources — Hacker News, Kagi News, RSS feeds — and archives them into beautifully formatted, offline-readable documents in HTML, Markdown, Gemtext, EPUB, and Sleepscreen formats.

**Website:** [inkfeed.cc](https://inkfeed.cc) | **Docs:** [docs.inkfeed.cc](https://docs.inkfeed.cc)

## Quick Start

```bash
pip install -e .
inkfeed
```

Or with Docker:

```bash
docker compose up --build
```

## Features

- **Multi-source** — Hacker News, Kagi News, and any RSS feed
- **Multi-format** — HTML, Markdown, Gemtext, EPUB, Sleepscreen (e-ink)
- **Self-contained** — images downloaded and embedded locally
- **Configurable** — TOML-based configuration for everything
- **Parallel** — concurrent fetching with configurable worker count
- **Resilient** — automatic retries on transient failures

## Configuration

Copy and edit `config.toml` to customize sources, output formats, and other settings. See the [configuration docs](https://docs.inkfeed.cc/getting-started/configuration/) for details.

## Optional Dependencies

```bash
# Development tools
pip install -e ".[dev]"

# Sleepscreen output (e-ink displays)
pip install -e ".[sleepscreen]"
playwright install chromium
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
