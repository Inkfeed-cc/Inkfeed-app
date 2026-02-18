from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TextColumn
from rich.rule import Rule

from inkfeed.archiver.base import ArchiveResult, GroupResult
from inkfeed.archiver.hackernews import HackerNewsArchiver
from inkfeed.archiver.kaginews import KagiNewsArchiver
from inkfeed.archiver.rss import RSSArchiver
from inkfeed.config import SourceConfig, load_config
from inkfeed.output.base import FormatWriter, IndexEntry
from inkfeed.output.epub import EpubWriter
from inkfeed.output.gemtext import GemtextWriter
from inkfeed.output.html import HtmlWriter
from inkfeed.output.markdown import MarkdownWriter
from inkfeed.output.sleepscreen import SleepscreenWriter
from inkfeed.utils.images import download_images

ARCHIVER_MAP = {
    "hackernews": HackerNewsArchiver,
    "kaginews": KagiNewsArchiver,
    "rss": RSSArchiver,
}

WRITER_MAP: dict[str, type[FormatWriter]] = {
    "html": HtmlWriter,
    "md": MarkdownWriter,
    "gemtext": GemtextWriter,
    "epub": EpubWriter,
    "sleepscreen": SleepscreenWriter,
}

console = Console()


def _resolve_config_path() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    return Path("config.toml")


def main() -> None:
    config_path = _resolve_config_path()
    if not config_path.exists():
        console.print(f"[red]Config file not found:[/red] {config_path}")
        sys.exit(1)

    config = load_config(config_path)
    enabled = [s for s in config.sources if s.enabled]
    console.print(Rule(f"[bold]Inkfeed[/bold] — {len(enabled)} source(s)"))

    date_str = datetime.now().strftime("%Y-%m-%d")
    output_formats = config.output_formats or ["html"]

    # Instantiate and set up writers.
    writers: list[FormatWriter] = []
    for fmt in output_formats:
        writer_cls = WRITER_MAP.get(fmt)
        if writer_cls is None:
            console.print(f"  [yellow]warn[/yellow] unknown format: {fmt}")
            continue
        w = writer_cls(config)
        w.setup()
        writers.append(w)

    index_entries: dict[str, list[IndexEntry]] = {w.name: [] for w in writers}

    for source in config.sources:
        if not source.enabled:
            console.print(f"  [dim]skip[/dim] {source.display_name}")
            continue

        archiver_cls = ARCHIVER_MAP.get(source.name) or ARCHIVER_MAP.get(source.type)
        if archiver_cls is None:
            console.print(f"  [yellow]warn[/yellow] {source.display_name}: no archiver registered")
            continue

        entries = _run_source(
            source,
            archiver_cls,
            config.output_dir,
            date_str=date_str,
            writers=writers,
            max_workers=config.max_workers,
            max_retries=config.max_retries,
        )
        for name, fmt_entries in entries.items():
            if name in index_entries:
                index_entries[name].extend(fmt_entries)

    # Write indices and tear down writers.
    for w in writers:
        fmt_entries = index_entries.get(w.name, [])
        if fmt_entries:
            date_dir = config.output_dir / w.name / date_str
            date_dir.mkdir(parents=True, exist_ok=True)

            for entry in fmt_entries:
                if entry.children:
                    source_dir = date_dir / Path(entry.rel_link).parent
                    source_dir.mkdir(parents=True, exist_ok=True)
                    w.write_source_index(
                        source_dir, entry.display_name, date_str, entry.children,
                    )

            w.write_date_index(date_dir, date_str, fmt_entries)

        w.teardown()

    console.print(Rule("[green]done[/green]"))


def _run_source(
    source: SourceConfig,
    archiver_cls: type,
    output_dir: Path,
    *,
    date_str: str,
    writers: list[FormatWriter],
    max_workers: int = 8,
    max_retries: int = 3,
) -> dict[str, list[IndexEntry]]:
    """Run a single source archiver and write output in all formats.

    Returns a dict mapping format name to a list of :class:`IndexEntry`
    objects produced by each writer.
    """
    entries: dict[str, list[IndexEntry]] = {w.name: [] for w in writers}

    try:
        console.print(f"\n[bold cyan]{source.display_name}[/bold cyan]")
        archiver = archiver_cls(source, output_dir)
        result: ArchiveResult = archiver.run(
            max_workers=max_workers, max_retries=max_retries,
        )

        # Download images for every group (format-independent).
        for group in result.groups:
            _download_group_images(
                group, max_workers=max_workers, max_retries=max_retries,
            )

        # Write output for each writer.
        for w in writers:
            fmt_entries = w.write_source(result, output_dir, date_str)
            entries[w.name].extend(fmt_entries)

        for group in result.groups:
            fmt_list = ", ".join(w.name for w in writers)
            console.print(
                f"  [green]\u2713[/green] {group.display_name}:"
                f" {len(group.articles)} articles ({fmt_list})"
            )

    except Exception as e:
        console.print(f"  [red]\u2717 {source.display_name} failed:[/red] {e}")

    return entries


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _download_group_images(
    group: GroupResult,
    *,
    max_workers: int = 8,
    max_retries: int = 3,
) -> None:
    """Download images for all articles in *group* into its cache dir."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]{task.fields[title]}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(
            "downloading images", total=len(group.articles), title="",
        )
        for article in group.articles:
            progress.update(task, title=article.title[:50])
            article.content_html = download_images(
                article.content_html, group.cache_dir,
                max_workers=max_workers, max_retries=max_retries,
            )
            progress.advance(task)


if __name__ == "__main__":
    main()
