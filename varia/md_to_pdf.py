#!/usr/bin/env python3
# Commands:
# pip install markdown weasyprint
# python varia/md_to_pdf.py OUTPUT.pdf
# python varia/md_to_pdf.py --dir /path/to/dir OUTPUT.pdf

"""Convert all Markdown files in a directory to a single PDF file.

Files are included in alphabetical order (case-insensitive sort by filename).
Only top-level .md files are collected (non-recursive).
Note: relative image paths in Markdown files may not render correctly in the PDF.
"""

import argparse
import sys
from pathlib import Path

import markdown
from weasyprint import CSS, HTML

PAGE_CSS = """
@page {
    margin: 2cm;
}
body {
    font-family: DejaVu Sans, Noto Sans, Liberation Sans, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}
h1, h2, h3, h4, h5, h6 {
    font-family: DejaVu Sans, Noto Sans, Liberation Sans, Arial, sans-serif;
    color: #111;
    margin-top: 1.4em;
    margin-bottom: 0.4em;
}
code, pre {
    font-family: DejaVu Sans Mono, Liberation Mono, Courier New, monospace;
    background: #f5f5f5;
    font-size: 0.9em;
}
code {
    padding: 0.15em 0.35em;
    border-radius: 3px;
}
pre {
    padding: 0.8em 1em;
    overflow-x: auto;
}
blockquote {
    border-left: 3px solid #ccc;
    margin: 0;
    padding-left: 1em;
    color: #555;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}
th, td {
    border: 1px solid #ccc;
    padding: 0.4em 0.7em;
    text-align: left;
}
th {
    background: #f0f0f0;
}
.section-source {
    font-size: 8pt;
    color: #888;
    margin-top: 2em;
    margin-bottom: 0.2em;
    font-style: italic;
}
.section-divider {
    border: none;
    border-top: 1px solid #ddd;
    margin: 2em 0 1em 0;
}
"""

MD_EXTENSIONS = ["extra", "sane_lists"]


def collect_md_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.md"), key=lambda p: p.name.casefold())


def read_file(filepath: Path) -> str | None:
    """Try to read a file as UTF-8 or UTF-8-SIG. Returns None if unreadable."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            content = filepath.read_text(encoding=encoding)
            if "\x00" in content:
                print(
                    f"  Warning: '{filepath.name}' appears to be a binary file, skipping.",
                    file=sys.stderr,
                )
                return None
            return content
        except UnicodeDecodeError:
            continue
        except OSError as e:
            print(f"  Warning: cannot read '{filepath.name}': {e}", file=sys.stderr)
            return None
    print(
        f"  Warning: '{filepath.name}' is not valid UTF-8, skipping.",
        file=sys.stderr,
    )
    return None


def md_to_html(content: str, filename: str) -> str | None:
    """Convert Markdown content to HTML fragment. Returns None on error."""
    try:
        return markdown.markdown(content, extensions=MD_EXTENSIONS, tab_length=2)
    except Exception as e:
        print(
            f"  Warning: failed to parse '{filename}' as Markdown: {e}",
            file=sys.stderr,
        )
        return None


def build_html(sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for i, (filename, body_html) in enumerate(sections):
        if i > 0:
            parts.append('<hr class="section-divider">')
        parts.append(f'<p class="section-source">{filename}</p>')
        parts.append(body_html)

    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="mul">
<head>
  <meta charset="utf-8">
  <title>Markdown Export</title>
</head>
<body>
{body}
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert all Markdown files in a directory to a single PDF."
    )
    parser.add_argument("output", help="Output PDF file path")
    parser.add_argument(
        "--dir",
        default=".",
        metavar="DIRECTORY",
        help="Directory containing .md files (default: current directory)",
    )
    args = parser.parse_args()

    directory = Path(args.dir).resolve()
    output = Path(args.output)

    if not directory.is_dir():
        print(f"Error: '{args.dir}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    md_files = collect_md_files(directory)
    if not md_files:
        print(f"No .md files found in '{directory}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(md_files)} file(s) in '{directory}':")

    sections: list[tuple[str, str]] = []
    skipped: list[str] = []

    for filepath in md_files:
        print(f"  Processing: {filepath.name}")
        content = read_file(filepath)
        if content is None:
            skipped.append(filepath.name)
            continue
        html_body = md_to_html(content, filepath.name)
        if html_body is None:
            skipped.append(filepath.name)
            continue
        sections.append((filepath.name, html_body))

    if skipped:
        print(f"\nSkipped {len(skipped)} file(s) due to errors:")
        for name in skipped:
            print(f"  - {name}")

    if not sections:
        print("Error: no valid Markdown files to convert.", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerating PDF from {len(sections)} file(s)...")
    try:
        combined_html = build_html(sections)
        HTML(string=combined_html, base_url=str(directory)).write_pdf(
            output, stylesheets=[CSS(string=PAGE_CSS)]
        )
        print(f"PDF written to: {output}")
    except Exception as e:
        print(f"Error: failed to generate PDF: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
