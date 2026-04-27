import asyncio
from pathlib import Path

from weasyprint import CSS, HTML


DEFAULT_FONT_CSS = """
html, body {
    font-family: "DejaVu Sans", "Noto Sans", sans-serif;
}
"""


def _ensure_utf8_meta(html_content: str) -> str:
    lowered = html_content.lower()
    if "charset=" in lowered:
        return html_content

    head_open = lowered.find("<head>")
    if head_open != -1:
        insert_at = head_open + len("<head>")
        return (
            html_content[:insert_at]
            + '\n  <meta charset="utf-8">\n'
            + html_content[insert_at:]
        )

    return '<meta charset="utf-8">\n' + html_content


def _write_pdf(html_path: Path, pdf_path: Path) -> None:
    html_content = _ensure_utf8_meta(html_path.read_text(encoding="utf-8"))
    HTML(
        string=html_content,
        base_url=str(html_path.parent),
        encoding="utf-8",
    ).write_pdf(
        str(pdf_path),
        stylesheets=[CSS(string=DEFAULT_FONT_CSS)],
    )


async def convert_html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    await asyncio.to_thread(_write_pdf, html_path, pdf_path)
