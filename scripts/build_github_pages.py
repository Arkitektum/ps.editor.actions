"""Build a static site for product specifications using Designsystemet styles."""

from __future__ import annotations

import argparse
import html
import re
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Iterable

import markdown
import yaml


_MARKDOWN_EXTENSIONS = [
    "extra",
    "sane_lists",
    "admonition",
    "toc",
]

_HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang=\"no\" data-theme=\"digdir\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>$page_title</title>
    <meta name=\"description\" content=$page_description />
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/@digdir/designsystemet-css@1.6.0/dist/src/index.css\" integrity=\"sha384-XFjU1ON2Tn7gVe20jrkLTcttGZN5EoIbB1bzLtn8JCzfTYDltv46ytrDiAjcYENV\" crossorigin=\"anonymous\" />
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/@digdir/designsystemet-theme@1.6.0/src/themes/designsystemet.css\" integrity=\"sha384-3uAT5IuMDqQqM1uVQs7tRSZmVd6WzJKFP3+3UbG8Ghy8oAJyX+FI5HGyl2zWphyC\" crossorigin=\"anonymous\" />
    <link rel=\"stylesheet\" href=\"https://altinncdn.no/fonts/inter/v4.1/inter.css\" integrity=\"sha384-OcHzc/By/OPw9uJREawUCjP2inbOGKtKb4A/I2iXxmknUfog2H8Adx71tWVZRscD\" crossorigin=\"anonymous\" />
    <style>
      :root {
        color-scheme: light;
      }

      body {
        margin: 0;
        font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: var(--ds-color-background-default, #ffffff);
        color: var(--ds-color-text-default, #1a1a1a);
      }

      a {
        color: var(--ds-color-accent-text-default, #1b51a1);
      }

      a:hover,
      a:focus {
        text-decoration: underline;
      }

      .page-shell {
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        background: var(--ds-color-background-subtle, #f6f6f6);
      }

      .page-section {
        width: min(75rem, calc(100% - 2rem));
        margin: 0 auto;
        padding: clamp(1.5rem, 3vw, 3rem) 0;
      }

      .page-main {
        display: grid;
        gap: clamp(1.5rem, 3vw, 3rem);
        grid-template-columns: minmax(16rem, 20rem) minmax(0, 1fr);
        align-items: start;
      }

      .page-header {
        padding-bottom: clamp(1rem, 2vw, 2.5rem);
      }

      .page-header__kicker {
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--ds-color-text-subtle, #4f4f4f);
        margin: 0 0 0.5rem 0;
      }

      .page-header h1 {
        font-size: clamp(2rem, 4vw, 2.8rem);
        line-height: 1.1;
        margin: 0;
      }

      .page-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
        margin-top: 1rem;
        color: var(--ds-color-text-subtle, #4f4f4f);
      }

      .breadcrumbs {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        font-size: 0.95rem;
        margin-top: 1.5rem;
        padding: 0;
        list-style: none;
      }

      .breadcrumbs li::after {
        content: '\203A';
        margin: 0 0.5rem;
        color: var(--ds-color-text-subtle, #4f4f4f);
      }

      .breadcrumbs li:last-child::after {
        content: '';
        margin: 0;
      }

      .toc {
        background: var(--ds-color-surface-subtle, #ffffff);
        border-radius: 1rem;
        padding: clamp(1.25rem, 2.5vw, 2rem);
        box-shadow: 0 1px 2px rgb(15 23 42 / 0.08);
        position: sticky;
        top: clamp(1rem, 3vw, 2rem);
        align-self: start;
      }

      .toc h2 {
        margin-top: 0;
        font-size: 1.1rem;
      }

      .toc ul {
        list-style: none;
        padding-left: 0;
        margin: 0;
      }

      .toc li {
        margin: 0.25rem 0;
      }

      .toc ul ul {
        padding-left: 1.25rem;
        border-left: 2px solid var(--ds-color-border-subtle, #d1d5db);
        margin-top: 0.5rem;
      }

      .article-card {
        background: var(--ds-color-surface-default, #ffffff);
        border-radius: 1.25rem;
        box-shadow: 0 16px 48px rgb(15 23 42 / 0.08);
      }

      .article-card__inner {
        padding: clamp(1.5rem, 3vw, 2.5rem);
      }

      .article-content h1,
      .article-content h2,
      .article-content h3,
      .article-content h4,
      .article-content h5,
      .article-content h6 {
        color: var(--ds-color-text-default, #1a1a1a);
        margin-top: clamp(1.5rem, 3vw, 2.5rem);
        margin-bottom: 0.5rem;
      }

      .article-content h2 {
        border-bottom: 1px solid var(--ds-color-border-subtle, #e5e7eb);
        padding-bottom: 0.4rem;
      }

      .article-content p,
      .article-content ul,
      .article-content ol {
        line-height: 1.7;
        margin-bottom: 1rem;
      }

      .article-content table {
        width: 100%;
        border-collapse: collapse;
        margin: 1.5rem 0;
        box-shadow: inset 0 0 0 1px var(--ds-color-border-subtle, #e5e7eb);
      }

      .article-content th,
      .article-content td {
        border: 1px solid var(--ds-color-border-subtle, #e5e7eb);
        padding: 0.75rem 1rem;
        text-align: left;
      }

      .article-content pre {
        background: var(--ds-color-surface-subtle, #f3f4f6);
        padding: 1rem;
        border-radius: 0.75rem;
        overflow-x: auto;
      }

      .article-content code {
        font-family: 'Fira Code', 'SFMono-Regular', Menlo, Consolas, monospace;
      }

      .page-footer {
        margin-top: auto;
        padding-block: clamp(1.5rem, 3vw, 2.5rem);
        color: var(--ds-color-text-subtle, #4f4f4f);
        font-size: 0.95rem;
      }

      .page-footer a {
        color: inherit;
      }

      @media (max-width: 64rem) {
        .page-main {
          grid-template-columns: minmax(0, 1fr);
        }

        .toc {
          position: relative;
          top: auto;
          margin-bottom: clamp(1.5rem, 3vw, 2.5rem);
        }
      }

      @media (max-width: 48rem) {
        .page-section {
          width: calc(100% - 1.5rem);
        }

        .article-card__inner {
          padding: 1.25rem;
        }
      }
    </style>
  </head>
  <body>
    <a class=\"ds-sr-only\" href=\"#innhold\">Hopp til innhold</a>
    <div class=\"page-shell\">
      <header class=\"page-section page-header\">
        <p class=\"page-header__kicker\">Produktspesifikasjon</p>
        <h1>$title</h1>
        $meta_block
        $breadcrumbs_block
      </header>
      <main id=\"innhold\" class=\"page-section\">
        <div class=\"page-main\">
$toc_block          <article class=\"article-card\">
            <div class=\"article-card__inner article-content\">
              $content
            </div>
          </article>
        </div>
      </main>
      <footer class=\"page-section page-footer\">
        <p>Bygget automatisk med Designsystemet for publisering på GitHub Pages.</p>
      </footer>
    </div>
  </body>
</html>
"""
)

_INDEX_TEMPLATE = Template(
    """<!doctype html>
<html lang=\"no\" data-theme=\"digdir\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Produktspesifikasjoner</title>
    <meta name=\"description\" content=\"Oversikt over genererte produktspesifikasjoner\" />
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/@digdir/designsystemet-css@1.6.0/dist/src/index.css\" integrity=\"sha384-XFjU1ON2Tn7gVe20jrkLTcttGZN5EoIbB1bzLtn8JCzfTYDltv46ytrDiAjcYENV\" crossorigin=\"anonymous\" />
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/@digdir/designsystemet-theme@1.6.0/src/themes/designsystemet.css\" integrity=\"sha384-3uAT5IuMDqQqM1uVQs7tRSZmVd6WzJKFP3+3UbG8Ghy8oAJyX+FI5HGyl2zWphyC\" crossorigin=\"anonymous\" />
    <link rel=\"stylesheet\" href=\"https://altinncdn.no/fonts/inter/v4.1/inter.css\" integrity=\"sha384-OcHzc/By/OPw9uJREawUCjP2inbOGKtKb4A/I2iXxmknUfog2H8Adx71tWVZRscD\" crossorigin=\"anonymous\" />
    <style>
      body {
        margin: 0;
        font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: var(--ds-color-background-subtle, #f6f6f6);
        color: var(--ds-color-text-default, #1a1a1a);
      }

      .page-section {
        width: min(75rem, calc(100% - 2rem));
        margin: 0 auto;
        padding: clamp(2rem, 4vw, 3.5rem) 0;
      }

      .hero {
        text-align: center;
        padding-top: clamp(3rem, 6vw, 5rem);
      }

      .hero h1 {
        font-size: clamp(2.5rem, 5vw, 3.5rem);
        margin-bottom: 0.75rem;
      }

      .spec-grid {
        display: grid;
        gap: clamp(1.5rem, 3vw, 2.5rem);
        grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
        margin-top: clamp(2rem, 4vw, 3rem);
      }

      .spec-card {
        background: var(--ds-color-surface-default, #ffffff);
        border-radius: 1.25rem;
        padding: clamp(1.5rem, 3vw, 2.5rem);
        box-shadow: 0 16px 48px rgb(15 23 42 / 0.08);
        text-align: left;
      }

      .spec-card h2 {
        margin-top: 0;
        font-size: 1.4rem;
        margin-bottom: 0.5rem;
      }

      .spec-card p {
        margin: 0.25rem 0 0 0;
        color: var(--ds-color-text-subtle, #4f4f4f);
      }

      .spec-card a {
        text-decoration: none;
        color: inherit;
      }

      .spec-card a:focus,
      .spec-card a:hover {
        text-decoration: underline;
      }

      .spec-empty {
        grid-column: 1 / -1;
        margin: 0;
        background: var(--ds-color-surface-default, #ffffff);
        border-radius: 1.25rem;
        padding: clamp(1.5rem, 3vw, 2.5rem);
        box-shadow: 0 16px 48px rgb(15 23 42 / 0.08);
        color: var(--ds-color-text-subtle, #4f4f4f);
        text-align: center;
      }
    </style>
  </head>
  <body>
    <main class=\"page-section\">
      <header class=\"hero\">
        <p class=\"page-header__kicker\">Produktspesifikasjoner</p>
        <h1>Tilgjengelige dokumenter</h1>
        <p>Utforsk de publiserte produktspesifikasjonene fra data/produktspesifikasjon.</p>
      </header>
      <section class=\"spec-grid\">
        $items
      </section>
    </main>
  </body>
</html>
"""
)


@dataclass
class PageMetadata:
    """Front matter metadata for a product specification."""

    title: str
    updated: str | None
    description: str | None


def _parse_front_matter(text: str) -> tuple[PageMetadata, str]:
    """Split Markdown text into metadata and body parts."""

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            raw_meta = text[3:end]
            body = text[end + 4 :]
            try:
                data = yaml.safe_load(raw_meta) or {}
            except yaml.YAMLError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            title = str(data.get("title", "Produktspesifikasjon"))
            updated = data.get("updated")
            description = data.get("description")
            if description is not None:
                description = str(description)
            meta = PageMetadata(title=title, updated=updated, description=description)
            return meta, body.lstrip("\n")

    meta = PageMetadata(title="Produktspesifikasjon", updated=None, description=None)
    return meta, text


def _format_updated(value: str | None) -> str:
    if not value:
        return ""

    try:
        parsed = datetime.fromisoformat(value).date()
    except ValueError:
        return html.escape(value)

    return parsed.strftime("%d.%m.%Y")


def _render_toc(tokens: list[dict[str, object]] | None) -> str:
    if not tokens:
        return ""

    def render_items(items: Iterable[dict[str, object]]) -> str:
        parts: list[str] = ["<ul>"]
        for item in items:
            slug = html.escape(str(item.get("id", "")))
            title = html.escape(str(item.get("name", "")))
            parts.append(f'<li><a href="#{slug}">{title}</a>')
            children = item.get("children")
            if isinstance(children, list) and children:
                parts.append(render_items(children))
            parts.append("</li>")
        parts.append("</ul>")
        return "".join(parts)

    return (
        "<aside class=\"toc\" aria-label=\"Innhold\">"
        "<h2>Innhold</h2>"
        f"{render_items(tokens)}"
        "</aside>"
    )


def _render_breadcrumbs(items: list[tuple[str, str | None]]) -> str:
    if not items:
        return ""

    parts: list[str] = ["<nav aria-label=\"Brødsmulesti\">", "<ul class=\"breadcrumbs\">"]
    for label, href in items:
        name = html.escape(label)
        if href:
            parts.append(f"<li><a href=\"{href}\">{name}</a></li>")
        else:
            parts.append(f"<li aria-current=\"page\">{name}</li>")
    parts.append("</ul></nav>")
    return "".join(parts)


def _parse_markdown_asset_target(target: str) -> str | None:
    """Extract the path component from a Markdown image target.

    Markdown image syntax allows optional titles following the URL, for example
    ``![alt](path/to/image.png "Title")``.  GitHub Pages builds only need the
    first part – the actual relative path – and should ignore the optional
    title.  This helper strips the title information and returns only the
    usable path.
    """

    target = target.strip()
    if not target:
        return None

    if target.startswith("<"):
        closing = target.find(">")
        if closing != -1:
            target = target[1:closing].strip()
        else:
            target = target[1:].strip()
    else:
        try:
            parts = shlex.split(target)
        except ValueError:
            parts = target.split()
        if parts:
            target = parts[0]
        else:
            target = ""

    target = target.strip().strip('"\'')
    if not target:
        return None

    return target


def _extract_assets(markdown_text: str) -> set[str]:
    """Return a set of relative asset paths referenced in the Markdown body."""

    pattern_md = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    pattern_img = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.IGNORECASE)
    assets: set[str] = set()

    for match in pattern_md.findall(markdown_text):
        target = _parse_markdown_asset_target(match)
        if not target:
            continue
        path = target.split("?")[0].split("#")[0].strip()
        if path and not path.startswith(("http://", "https://", "data:")):
            assets.add(path)

    for match in pattern_img.findall(markdown_text):
        path = match.split("?")[0].split("#")[0].strip()
        if path and not path.startswith(("http://", "https://", "data:")):
            assets.add(path)

    return assets


def _copy_assets(asset_paths: set[str], source_dir: Path, output_dir: Path) -> None:
    for relative in asset_paths:
        source_path = (source_dir / relative).resolve()
        try:
            source_path.relative_to(source_dir.resolve())
        except ValueError:
            # Skip assets outside the specification directory to avoid leaking files.
            continue
        if not source_path.exists():
            continue
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)


def _render_page(markdown_path: Path, output_dir: Path, source_root: Path) -> PageMetadata:
    text = markdown_path.read_text(encoding="utf-8")
    metadata, body = _parse_front_matter(text)

    md = markdown.Markdown(extensions=_MARKDOWN_EXTENSIONS)
    html_content = md.convert(body)
    toc_tokens = getattr(md, "toc_tokens", None)
    toc_block = ""
    if toc_tokens:
        toc_html = _render_toc(toc_tokens)
        toc_block = f"          {toc_html}\n"

    try:
        relative_dir = markdown_path.parent.relative_to(source_root)
        depth = len(relative_dir.parts)
        root_href = "../" * depth or "./"
        crumb_items = [("Produktspesifikasjon", root_href)]
    except ValueError:
        crumb_items = [("Produktspesifikasjon", "./")]

    crumb_items.append((metadata.title, None))
    breadcrumbs = _render_breadcrumbs(crumb_items)

    updated_text = _format_updated(metadata.updated)
    meta_block = ""
    if updated_text:
        meta_block = f"<div class=\"page-meta\"><span>Sist oppdatert: {html.escape(updated_text)}</span></div>"

    description = metadata.description or metadata.title
    page_html = _HTML_TEMPLATE.substitute(
        page_title=html.escape(metadata.title),
        page_description=html.escape(description),
        title=html.escape(metadata.title),
        meta_block=meta_block,
        breadcrumbs_block=breadcrumbs,
        toc_block=toc_block,
        content=html_content,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(page_html, encoding="utf-8")

    assets = _extract_assets(body)
    _copy_assets(assets, markdown_path.parent, output_dir)

    return metadata


def _render_index(pages: list[tuple[str, str, str | None]] | None, output_dir: Path) -> None:
    cards: list[str] = []
    if pages:
        for title, href, updated in sorted(pages, key=lambda item: item[0].lower()):
            updated_text = _format_updated(updated)
            updated_html = f"<p>Sist oppdatert: {html.escape(updated_text)}</p>" if updated_text else ""
            cards.append(
                "<article class=\"spec-card\">"
                f"<a href=\"{html.escape(href)}\">"
                f"<h2>{html.escape(title)}</h2>"
                f"{updated_html}"
                "</a>"
                "</article>"
            )
        items_html = "".join(cards)
    else:
        items_html = "<p class=\"spec-empty\">Ingen produktspesifikasjoner er tilgjengelige ennå.</p>"

    listing = _INDEX_TEMPLATE.substitute(items=items_html)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(listing, encoding="utf-8")


def build_site(source_dir: Path, output_dir: Path) -> None:
    """Render all ``index.md`` files below ``source_dir`` to a static site."""

    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[tuple[str, str, str | None]] = []

    for markdown_path in sorted(source_dir.rglob("index.md")):
        rel_dir = markdown_path.parent.relative_to(source_dir)
        destination_dir = output_dir / rel_dir
        metadata = _render_page(markdown_path, destination_dir, source_dir)
        href = "/".join(rel_dir.parts) + "/" if rel_dir.parts else "./"
        pages.append((metadata.title, href, metadata.updated))

    _render_index(pages, output_dir)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default=Path("data/produktspesifikasjon"),
        type=Path,
        help="Rotkatalog for markdown-filer (standard: data/produktspesifikasjon).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("site"),
        help="Katalog der den statiske nettsiden skal skrives (standard: site).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv) if argv is not None else None)

    try:
        build_site(args.source, args.output)
    except OSError as exc:
        print(f"Feil under bygging av nettsiden: {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
