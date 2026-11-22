"""Build the User Guide and Developer Guide as print-ready HTML and PDF.

    pip install markdown                       # once
    python scripts/build_docs.py               # writes docs/pdf/*.html (+ *.pdf if Playwright is available)

PDF output uses Playwright for Python if installed:
    pip install playwright && playwright install chromium
Without it, open docs/pdf/*.html in Chrome/Edge and use Print → Save as PDF (A4, margins: default, background graphics on).
"""
from __future__ import annotations

import datetime as dt
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "pdf"

GUIDES = [
    ("USER_GUIDE.md", "LeadAutomationPipeline-User-Guide", "User Guide",
     "Set up, use and run your 24/7 lead pipeline: finding leads worldwide, every channel, follow-ups, clients."),
    ("DEVELOPER_GUIDE.md", "LeadAutomationPipeline-Developer-Guide", "Developer Guide",
     "Architecture, data model, modules, configuration, extending, testing and deployment."),
]

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
:root { --ink:#1b1f24; --muted:#5d6673; --line:#dfe3e8; --accent:#2d5bd8; --soft:#eef2fc; --code:#f4f6f8; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: Inter, "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; color: var(--ink);
       font-size: 10.4pt; line-height: 1.5; margin: 0; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always;
         border-left: 8px solid var(--accent); padding-left: 14mm; }
.cover .kicker { color: var(--accent); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 11pt; }
.cover h1 { font-size: 34pt; line-height: 1.1; margin: 6mm 0 4mm; border: none; padding: 0; }
.cover p { font-size: 13pt; color: var(--muted); max-width: 140mm; margin: 0 0 3mm; }
.cover .meta { margin-top: 14mm; font-size: 10pt; color: var(--muted); }
h1 { font-size: 20pt; margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 2px solid var(--accent); }
h2 { font-size: 15pt; margin: 9mm 0 3mm; padding-bottom: 1.5mm; border-bottom: 1px solid var(--line); page-break-after: avoid; }
h2:not(:first-of-type) { page-break-before: always; margin-top: 0; }
h3 { font-size: 12pt; margin: 6mm 0 2mm; page-break-after: avoid; color: #213a8a; }
p, ul, ol { margin: 0 0 2.6mm; }
li { margin-bottom: 1mm; }
a { color: var(--accent); text-decoration: none; }
strong { font-weight: 650; }
code { font-family: "JetBrains Mono", Consolas, "Cascadia Mono", Menlo, monospace; font-size: 8.8pt;
       background: var(--code); padding: .5px 4px; border-radius: 3px; }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 6px; padding: 3mm 4mm; overflow: hidden;
      white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; margin: 0 0 3mm; }
pre code { background: none; padding: 0; font-size: 8.4pt; line-height: 1.45; }
table { width: 100%; border-collapse: collapse; margin: 0 0 3.5mm; font-size: 9pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid var(--line); padding: 1.6mm 2.2mm; vertical-align: top; text-align: left; }
th { background: var(--soft); font-weight: 650; }
tbody tr:nth-child(even) td { background: #fafbfc; }
blockquote { margin: 0 0 3mm; padding: 2mm 4mm; border-left: 3px solid var(--accent); background: var(--soft); color: #2b3440; }
blockquote p:last-child { margin: 0; }
hr { border: none; border-top: 1px solid var(--line); margin: 5mm 0; }
.toc-note { color: var(--muted); font-size: 9pt; }
"""


def slugify(value: str, separator: str = "-") -> str:
    """GitHub-style heading ids, so the 'Contents' links inside the guides keep working."""
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


def to_html(md_path: Path, title: str, subtitle: str) -> str:
    import markdown

    text = md_path.read_text(encoding="utf-8")
    text = re.sub(r"^# .*\n", "", text, count=1)                    # the cover page carries the title
    # Links to other docs point to the GitHub copies (they still work from a PDF).
    repo = "https://github.com/NandaniRamoliya/LeadAutomationPipeline/blob/main/docs/"
    text = re.sub(r"\]\((?!https?://|#)([^)]+\.md)(#[^)]*)?\)", lambda m: f"]({repo}{m.group(1)}{m.group(2) or ''})", text)
    text = re.sub(r"\]\((templates/)\)", f"]({repo}templates)", text)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"],
                             extension_configs={"toc": {"slugify": slugify}})
    today = dt.date.today().strftime("%d %B %Y")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>LeadAutomationPipeline: {html.escape(title)}</title><style>{CSS}</style></head><body>
<section class="cover"><div class="kicker">LeadAutomationPipeline</div><h1>{html.escape(title)}</h1>
<p>{html.escape(subtitle)}</p><div class="meta">Version 1.1 · {today}<br>github.com/NandaniRamoliya/LeadAutomationPipeline</div></section>
<main>{body}</main></body></html>"""


FOOTER = ('<div style="font-size:8px;color:#8a93a0;width:100%;padding:0 16mm;display:flex;justify-content:space-between;'
          'font-family:Inter,Segoe UI,Arial,sans-serif"><span>LeadAutomationPipeline · {title}</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for src, stem, title, subtitle in GUIDES:
        out = OUT / f"{stem}.html"
        out.write_text(to_html(DOCS / src, title, subtitle), encoding="utf-8")
        made.append(out)
        print(f"wrote {out.relative_to(ROOT)}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright for Python not installed: open the HTML files in a browser and Print → Save as PDF,\n"
              "or: pip install playwright && playwright install chromium, then run this again.")
        return made
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for (src, stem, title, _), html_file in zip(GUIDES, made):
            page.goto(html_file.as_uri())
            page.wait_for_load_state("networkidle")
            pdf = OUT / f"{stem}.pdf"
            page.pdf(path=str(pdf), format="A4", print_background=True, display_header_footer=True,
                     header_template="<div></div>", footer_template=FOOTER.replace("{title}", title),
                     margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"})
            print(f"wrote {pdf.relative_to(ROOT)}")
        browser.close()
    return made


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
